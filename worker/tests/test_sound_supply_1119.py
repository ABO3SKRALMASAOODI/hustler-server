"""Round1 thin-sound (19 Nov 2026): a podcast short placed 1-4 sounds in 50 s because its events were not motivated.

- a picture that arrives already moving (the caller measured the clip's subject motion: ``moving`` on the shot) is
  something on screen that sounds, like a whip;
- a caller may name its own tick rate (supply ``tick_10s``): a talking film (class T) otherwise carries no tick layer
  and its display landings go unsounded. Without it the class's rate stands (customers unchanged).

Signal only: the owner's library, no voice, no model."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from score import sound as SD  # noqa: E402


def _l1(moving):
    items = [dict(id="take", k="shot", pic="main", f=[0, 60], src=[0, 2]),
             dict(id="clip", k="shot", pic="v01", f=[60, 120], src=[0, 2], **({"moving": True} if moving else {})),
             dict(id="back", k="shot", pic="main", f=[120, 300], src=[4, 10])]
    return dict(fps=30, dur_f=300, assets={"main": {"kind": "video"}, "v01": {"kind": "video"}}, items=items)


def test_a_clip_measured_moving_arrives_as_a_motivated_picture():
    plain = next(e for e in SD.l1_events(_l1(False)) if e["f"] == 60)
    moved = next(e for e in SD.l1_events(_l1(True)) if e["f"] == 60)
    assert plain["kind"] == "picture" and not SD.motivated(plain)
    assert moved["kind"] == "picture" and moved.get("moving") and SD.motivated(moved)


def _talking(tick_10s=None):
    fps, dur = 30, 40.0
    ev = [{"f": int(t * fps), "kind": "display", "size": 0.7} for t in (2.0, 7.5, 13.0, 18.5, 24.0, 29.5, 35.0)]
    ctx = {"fps": fps, "dur_f": int(dur * fps), "existing": [], "speech": None, "voice": None, "words": [(0.2, dur - 2.0)],
           "bed": True, "np10": 0.5, "seed": SD.film_seed("talk")}
    if tick_10s is not None:
        ctx["tick_10s"] = tick_10s
    return SD.plan_sounds(ev, ctx)


def test_a_talking_film_keeps_no_tick_layer_unless_its_caller_names_one():
    _s0, r0 = _talking()
    assert r0["class"] == "T" and r0["allow_ticks"] == 0
    s1, r1 = _talking(1.5)
    assert r1["class"] == "T" and r1["allow_ticks"] == 6
    assert len(r1["ticks"]) == 2 and not r1["dropped"]              # the two landings refused for "0 small ticks" now sound
    assert len(s1) > len(_talking()[0])
