"""The pacing driver's band keys for a family that cuts faster (valmera-stories, 14 Nov 2026): absent, the driver does
exactly what it did; "punch_first" lets the settle land on a picture's first framing on a stressed word, and
"ease_min_s" eases short framings with a 4-9% move."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scene import pace  # noqa: E402

FPS, W, H = 30.0, 1080, 1920


def _shots(n=12, length=15):
    out, f = [], 0
    for k in range(n):
        out.append({"id": f"s{k}", "f": [f, f + length], "pic": f"p{k}", "pw": 3000, "ph": 2000, "faces": [], "regions": []})
        f += length
    stress = [(s["f"][0] + d, 0.9) for s in out for d in (0, 7)]
    return out, stress


def _strip(items):
    return [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]


def test_absent_keys_change_nothing():
    shots, stress = _shots()
    a, la = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4)
    b, lb = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4, band={})
    assert _strip(a) == _strip(b) and la == lb
    assert la["eased"] == 0                                    # half-second framings: no eased move by default


def test_the_keys_punch_the_first_framing_and_ease_short_ones():
    shots, stress = _shots()
    _, log0 = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4)
    items, log = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4, band={"punch_first": True, "ease_min_s": 0.4})
    assert log["punches"] > log0["punches"] and log["eased"] > 0
    for it in items:
        to = (it.get("_kw") or {}).get("to")
        if to and it["f"][1] - it["f"][0] < int(1.1 * FPS):
            cw0 = it["_crop"][2] - it["_crop"][0]
            cw1 = to["crop"][2] - to["crop"][0]
            assert 0.03 <= cw0 / cw1 - 1 <= 0.095


def test_a_move_budget_holds_the_rolling_ten_seconds():
    """Round 2: band "moves_per_10s" (the references' 4.3) lets a punch, an eased move or a whip land only while the rolling
    10 s count is under it; without the key the output is exactly today's."""
    shots, stress = _shots(n=60, length=15)
    band = {"punch_first": True, "ease_min_s": 0.4}
    _, log0 = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4, band=band)
    items, log = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4, band=dict(band, moves_per_10s=4.3))
    moves = [m["f"][0] for m in log["moves"] if m.get("settle") or m.get("to")]
    moves += [it["f"][0] for it in items if it.get("shutter") == pace.WHIP_SHUTTER and it["f"][0] > 0]
    assert moves
    end = max(it["f"][1] for it in items)
    worst = max(sum(1 for m in moves if a <= m < a + 10 * FPS) for a in range(0, int(end), 5))
    assert worst <= 7 and log["punches"] + log["eased"] < log0["punches"] + log0["eased"]
    a, la = pace.drive([dict(s) for s in shots], stress, FPS, (W, H), "story", seed=4, band=band)
    assert la == log0                                     # without the key: exactly today's
