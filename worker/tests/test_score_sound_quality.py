"""The SFX quality filter (owner, 15 Nov 2026: "the sfx and the music was bad ... like a mountain fall sound").

score/sfx_quality.json carries lane STING-REF's library audit (signals only, no listening model): every flag per
recording and the recordings no film may sound (a long rumbling or debris tail, clipped tops, an audible encoder wall,
a sound a phone cannot play, a class the references never use). SND3 never picks one, by the ruler or by its fixed
table. Signal only: the owner's library, synthetic films, no model, no network."""
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from score import sound as SD  # noqa: E402
from score import sound_choice as choice  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")
Q = json.load(open(os.path.join(os.path.dirname(HERE), "score", "sfx_quality.json")))


def test_the_audit_is_in_step_with_the_library():
    lib = SD.library()
    bad = SD.excluded()
    # the rumbling stings and every flag a listener hears are out
    assert {"impact_10", "impact_2", "impact_3", "impact_4", "impact_5", "impact_6", "braam_2", "whoosh_3", "heartbeat_1",
            "riser_3", "click_1", "cash_register_1"} <= bad
    assert bad == set(Q["excluded"])
    for sid, row in Q["flags"].items():
        assert sid in lib and row["sha"] == lib[sid].get("sha"), sid       # judged on the very file the library holds
        kinds = [k for k in row["flags"] if Q["kinds"][k]["excludes"]]
        assert (sid in bad) == bool(kinds), sid
    # every pick role keeps recordings to choose from
    roles = {}
    for sid, x in lib.items():
        if sid not in bad:
            roles[x.get("role")] = roles.get(x.get("role"), 0) + 1
    assert roles.get("impact", 0) >= 3 and roles.get("whoosh", 0) >= 6 and roles.get("click", 0) >= 6 and roles.get("riser", 0) >= 6


def test_the_fixed_table_steps_past_a_flagged_recording():
    assert SD._free("impact_1", 10.0, []) == "impact_8"                    # sub only on a phone: the clean landing
    assert SD._free("riser_3", 10.0, []) == "riser_2"                      # riser_3 and riser_1 are both out
    assert SD._free("glitch_1", 10.0, []) == "glitch_2"
    assert SD._free("whoosh_soft_2", 10.0, []) == "whoosh_soft_2"          # an unflagged one is kept as it is


def _film(dur=50.0, fps=30, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    ev, f = [], 20
    while f < dur * fps - 40:
        ev.append({"f": f, "kind": "picture", "size": float(rng.choice([0.25, 0.45, 0.7, 0.95])), "still": bool(rng.random() < 0.3)})
        f += int(rng.integers(20, 36))
    for t in (0.4, 2.2, 9.5, 14.2, 21.0, 26.4, 33.3, 38.8, 44.1):
        ev.append({"f": int(t * fps) + 7, "kind": "display", "size": float(rng.choice([0.4, 0.7, 1.0]))})
    for t in (12.0, 30.2):
        ev.append({"f": int(t * fps) + 11, "kind": "card", "photo": True, "area": 0.3})
    ev.append({"f": int(18.0 * fps) + 5, "kind": "counter", "steps": [int(18.0 * fps) + 5 + 4 * k for k in range(6)]})
    for t in (6.4, 17.1, 24.6, 41.7):
        ev.append({"f": int(t * fps) + 13, "kind": "move"})
    ev.append({"f": int(25.0 * fps), "kind": "flash", "glitch": True})
    ev.sort(key=lambda e: e["f"])
    out = []
    for e in ev:
        if not out or e["f"] - out[-1]["f"] > 2:
            out.append(e)
    return out


def _plan(name, seed, dur=50.0, fps=30):
    ctx = {"fps": fps, "dur_f": int(dur * fps), "existing": [], "speech": None, "voice": None, "words": [(0.2, dur - 2.5)],
           "bed": True, "np10": 11.0, "turn_f": int(31.0 * fps), "seed": SD.film_seed(name)}
    return SD.plan_sounds(_film(dur, fps, seed), ctx)


@needs_ffmpeg
@pytest.mark.parametrize("name,seed", [("fedex", 0), ("uber", 1), ("nike", 2), ("toy story", 3)])
def test_sound_design_never_picks_a_flagged_recording(name, seed):
    sounds, rep = _plan(name, seed)
    picked = [s["s"] for s in sounds]
    assert picked and rep["placed"]
    assert not set(picked) & SD.excluded(), sorted(set(picked) & SD.excluded())
    assert any(SD.role_of(s) == "impact" for s in picked)                  # the heroes still land, on clean impacts


@needs_ffmpeg
def test_the_fixed_table_never_picks_a_flagged_recording(monkeypatch):
    def no_ruler(*a, **k):
        raise RuntimeError("no ruler")
    monkeypatch.setattr(choice, "describe_library", no_ruler)
    sounds, _rep = _plan("fallback", 0)
    picked = {s["s"] for s in sounds}
    assert picked and not picked & SD.excluded(), sorted(picked & SD.excluded())
