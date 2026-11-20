"""The palette's device landings reach the sound design (20 Nov 2026). A workflow's Director lays devices
(worker/scene/palette) whose landings ride the scene as ``device_events``: {f, kind, strength}. score.sound reads them:
a tear sounds as a glitch, a world swapped in behind the speaker as a picture landing, a soft change (a leak, colour
returning, a halo) not at all, so the sound amplifies the moments the devices make and nothing more. A scene without
``device_events`` (every customer film) gives the same events as before. Signal only: no voice, no model."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from score import sound as SD  # noqa: E402


def _l1(events=None):
    items = [dict(id="a", k="shot", pic="p1", f=[0, 60]), dict(id="b", k="shot", pic="p2", f=[60, 120]),
             dict(id="c", k="shot", pic="p1", f=[120, 240])]
    l1 = dict(fps=30, dur_f=240, assets={"p1": {"kind": "image", "path": "/x/a.png"}, "p2": {"kind": "image", "path": "/x/b.png"}},
              items=items)
    if events is not None:
        l1["device_events"] = events
    return l1


def test_a_scene_without_device_events_is_unchanged():
    assert SD.l1_events(_l1()) == SD.l1_events(_l1([]))
    assert not SD.device_landings(_l1())


def test_each_landing_sounds_as_what_the_device_does():
    ev = [dict(f=60, kind="glitch", strength=0.9, dev="rgb_split", id="d1"),
          dict(f=150, kind="swap", strength=0.8, dev="bg_swap", id="d2"),
          dict(f=200, kind="light", strength=0.6, dev="leak", id="d3"),
          dict(f=210, kind="white", strength=0.3, dev="white", id="d4"),
          dict(f=225, kind="neg_hold", strength=0.7, dev="neg_hold", id="d5")]
    got = {e["f"]: e for e in SD.device_landings(_l1(ev))}
    assert got[60]["kind"] == "flash" and got[60]["glitch"] and got[60]["size"] == 0.9
    assert got[150]["kind"] == "card" and got[150]["area"] == 1.0 and got[150]["photo"]
    assert 200 not in got and 210 not in got, "a light drifting in and a faint change do not sound"
    assert got[225]["kind"] == "flash" and not got[225]["glitch"]
    events = SD.l1_events(_l1(ev))
    at60 = [e for e in events if abs(e["f"] - 60) <= 2]
    assert len(at60) == 1 and at60[0].get("device") == "glitch", "the device's landing takes the plain cut's place"
    assert any(e.get("device") == "swap" for e in events)
    assert all(SD.motivated(e) for e in events if e.get("device"))


def test_device_landings_do_not_widen_the_allowance():
    """A device's landing competes for the film's sounds; it is not a new picture of the supply."""
    ev = [dict(f=f, kind="glitch", strength=0.9) for f in range(10, 230, 20)]
    plain = [e for e in SD.l1_events(_l1()) if e["kind"] in ("picture", "cut")]
    devs = [e for e in SD.l1_events(_l1(ev)) if e.get("device")]
    assert devs and len(plain) <= 2
    pics = [e for e in SD.l1_events(_l1(ev)) if not e.get("device") and
            (e["kind"] in ("picture", "cut") or (e["kind"] == "card" and e.get("area", 0) >= 0.35))]
    assert len(pics) <= len(plain)
