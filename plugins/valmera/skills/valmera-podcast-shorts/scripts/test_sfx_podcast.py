"""Podcast shorts (wf1) never place a flagged library recording (owner, 15 Nov 2026; worker/sfx_filter.py): the payoff
sound was impact_1 (sub only on a phone) and two recipes asked for heartbeat_1 (clipped, sub only, outside the
references' classes); each now plays its nearest clean recording."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "alien"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "_shell"))
import brand  # noqa: E402
import earned as E  # noqa: E402

MAN = json.load(open(os.path.join(brand.worker_dir(), "sound_library", "manifest.json")))


def test_the_payoff_and_the_recipes_heartbeat_play_clean_recordings():
    SF = E.sfx_filter()
    events = [(30, "picture"), (300, "design"), (600, "payoff")]
    for table in (None, dict(payoff="heartbeat_1")):
        l1 = {"fps": 30, "dur_f": 900, "sound": []}
        rep = E.place_sounds(l1, events, MAN, -18.0, 8.0, table)
        played = {s["s"] for s in l1["sound"]}
        assert rep["placed"] >= 1 and not played & SF.flagged(), played
        pay = [s for s in l1["sound"] if s["role"] == "payoff"]
        assert pay and pay[0]["s"] == SF.nearest((table or {}).get("payoff") or E.SOUNDS["payoff"])
