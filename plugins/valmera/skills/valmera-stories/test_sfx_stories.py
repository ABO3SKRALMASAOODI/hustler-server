"""Stories (wf3) never place a flagged library recording (owner, 15 Nov 2026; worker/sfx_filter.py, lane STING-REF's
signal audit): its default sound table offered impact_1 and cash_register_1; each now plays its nearest clean one."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film as F  # noqa: E402
import pictures as PX  # noqa: E402


def test_the_default_sound_table_holds_no_flagged_recording():
    SF = PX.sfx_filter()
    lib = PX.sound_facts()
    assert {"impact_1", "cash_register_1"} <= set(lib)                 # the names the film asks for still answer
    for asked, m in lib.items():
        assert m["id"] not in SF.flagged() and m["file"] == m["id"] + ".flac", (asked, m)
        assert m["id"] == SF.nearest(asked) and m["asked"] == asked
    assert lib["impact_1"]["id"] == "impact_9" and lib["cash_register_1"]["id"] == "click_10"


def test_a_hit_on_a_flagged_name_places_its_replacement():
    SF = PX.sfx_filter()
    P = F.Plan({}, PX.sound_facts(("impact_1", "cash_register_1", "tick_1")))
    for name in ("impact_1", "cash_register_1", "tick_1"):
        P.hit(name, 90, -12.0)
    played = [s["s"] for s in P.sounds]
    assert played == ["impact_9", "click_10", "tick_1"]
    assert not {os.path.basename(s["file"])[:-5] for s in P.sounds} & SF.flagged()
    assert [s.get("asked") for s in P.sounds] == ["impact_1", "cash_register_1", None]
