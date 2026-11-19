"""Screen placement for the workflow films (lane sfx, 20 Nov 2026): every sound answers what the picture does.

The owner, 20 Nov: the SFX were "weird sounds ... not related at all". Measured on the night's films: a sound's class
came from the class the film had used least, not from the picture; a clock-tick slice filled a whoosh slot; a clip's
own subject motion made plain cuts "moving"; every display word took a different tick; ticks stood 10-31 dB over the
voice in its top band. Opt-in (VALMERA_SFX_PLACEMENT=screen, set by the workflow shell with its own set): a customer's
film keeps score/sound.py's placement.

Signals and the plan's own curves only: no voice model, no listening model, no words read."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from score import sound as SD  # noqa: E402
from score import sound_screen as SCR  # noqa: E402


def _row(sid, cls, sweep, c=1500.0, tonal=0.2, pitch=500.0, family=None, hf=-25.0, low=0.1):
    climb = 0.97 if cls == "riser" else 0.4                         # a riser is its climb, cut dead at the top
    return {"id": sid, "file": sid + ".flac", "family": family, "reference": {"class": cls},
            "screen": {"attack_s": sweep * climb, "tail_s": sweep * (1 - climb), "centroid_hz": c, "tonal": tonal, "pitch_hz": pitch,
                       "hf_db": hf, "low_share": low}}


LIB = {r["id"]: r for r in [
    _row("w_short_bright", "whoosh", 0.22, c=5000), _row("w_short_dark", "whoosh", 0.25, c=700),
    _row("w_mid", "whoosh", 0.5, c=1500), _row("w_long_dark", "whoosh", 1.0, c=600), _row("w_long_bright", "whoosh", 0.95, c=4000),
    _row("h_soft", "hit", 0.35), _row("h_firm", "hit", 0.4),
    _row("i_deep", "impact", 1.1, c=200, low=0.8), _row("i_mid", "impact", 0.7, c=600), _row("i_whoosh_in", "impact", 1.2),
    _row("r_long", "riser", 2.4), _row("r_short", "riser", 1.0),
    _row("c_ui", "click", 0.12, c=2600, hf=-19.0), _row("c_pop", "click", 0.15, c=1200), _row("c_tick", "click", 0.08, c=3500),
    _row("c_pitched", "click", 0.12, c=2500, tonal=0.9, pitch=900.0),
    _row("shutter_a", "click", 0.25, family="shutter"), _row("paper_a", "whoosh", 0.5, family="paper"),
    _row("glitch_a", "whoosh", 0.4, family="glitch"), _row("clock_tick", "click", 0.1, family="clock"),
]}


def _st(bed=True, seed=7):
    desc = {k: {"kind": v["reference"]["class"], "family": v.get("family")} for k, v in LIB.items()}
    return {"desc": desc, "seed": seed, "bed": bed}


def _pre(sid):
    return 0.5 if sid == "i_whoosh_in" else 0.0


def _layers(e, part="plain", tier="body", t=5.0, st=None):
    return SCR.layers(dict(e, _w=e.get("_w", 0.5)), part, tier, t, st if st is not None else _st(), LIB, _pre)


def test_off_unless_the_workflow_asks():
    os.environ.pop(SCR.MODE_ENV, None)
    assert not SCR.on(None) and not SCR.on({"np10": 2})
    assert SCR.on({"placement": "screen"})
    os.environ[SCR.MODE_ENV] = "screen"
    try:
        assert SCR.on(None) and not SCR.on({"placement": "library"})
    finally:
        os.environ.pop(SCR.MODE_ENV, None)


def test_a_sweep_is_as_long_as_the_move_and_as_bright_as_it_is_fast():
    fast, _ = _layers({"kind": "move", "f": 150, "travel_s": 0.1, "speed": 1.0})
    slow, _ = _layers({"kind": "move", "f": 150, "travel_s": 0.85, "speed": 0.1}, st=_st(seed=8))
    f_sid, s_sid = fast[-1][0], slow[-1][0]
    assert SCR.sweep_s(LIB[f_sid]) < 0.3 < 0.8 < SCR.sweep_s(LIB[s_sid])
    assert LIB[f_sid]["screen"]["centroid_hz"] > LIB[s_sid]["screen"]["centroid_hz"]


def test_type_lands_on_one_voice_and_a_pitched_click_gives_way():
    st = _st()
    voices = []
    for i in range(8):
        got, cls = _layers({"kind": "display", "f": 30 * (3 + 3 * i)}, tier="tick", t=3.0 + 3 * i, st=st)
        assert cls == "click"
        voices.append(got[0][0])
    assert len(set(voices)) <= SCR.PALETTE["click"]
    assert "c_pitched" not in voices and "clock_tick" not in voices


def test_an_object_answers_only_the_picture_that_shows_it():
    still, _ = _layers({"kind": "card", "f": 90, "area": 0.3, "still": True})
    assert LIB[still[0][0]].get("family") == "shutter"
    doc, _ = _layers({"kind": "card", "f": 90, "area": 0.3, "document": True})
    assert LIB[doc[0][0]].get("family") == "paper"
    flash, _ = _layers({"kind": "picture", "f": 90, "flash_cut": True, "glitch": True})
    assert LIB[flash[0][0]].get("family") == "glitch"
    for e in ({"kind": "card", "f": 90, "area": 0.3}, {"kind": "move", "f": 90, "travel_s": 0.3, "speed": 0.5},
              {"kind": "display", "f": 90}, {"kind": "picture", "f": 90, "flash_cut": True}):
        got, _ = _layers(e)
        assert got and not any(LIB[s].get("family") for s, _w in got), e


def test_a_hard_landing_is_a_hit_and_a_small_graphic_a_click():
    hit, cls = _layers({"kind": "card", "f": 90, "area": 0.5, "slam": True, "_w": 0.7}, tier="accent")
    assert cls in ("hit", "impact") and LIB[hit[0][0]]["reference"]["class"] == cls
    pop, cls = _layers({"kind": "card", "f": 90, "area": 0.04})
    assert cls == "click"


def test_a_riser_only_climbs_into_a_turn_the_song_does_not_carry_or_into_the_close():
    turn_bed, _ = _layers({"kind": "picture", "f": 600, "room_s": 3.0}, part="payoff", tier="hero", st=_st(bed=True))
    assert [w for _s, w in turn_bed] == ["hit"]                    # the song's own build and drop carry the turn
    turn_dry, _ = _layers({"kind": "picture", "f": 600, "room_s": 3.0}, part="payoff", tier="hero", st=_st(bed=False))
    assert [w for _s, w in turn_dry] == ["lead", "hit"] and LIB[turn_dry[0][0]]["reference"]["class"] == "riser"
    close, _ = _layers({"kind": "card", "f": 900, "room_s": 2.0}, part="close", tier="hero")
    assert LIB[close[0][0]]["reference"]["class"] == "riser" and LIB[close[0][0]]["screen"]["attack_s"] <= 2.0 - 0.2 + 0.25
    plain, _ = _layers({"kind": "move", "f": 300, "room_s": 4.0, "travel_s": 0.3, "speed": 0.5}, tier="accent")
    assert all(LIB[s]["reference"]["class"] != "riser" for s, _w in plain)
    hook, _ = _layers({"kind": "picture", "f": 30, "room_s": 1.0, "whip": True, "travel_s": 0.1, "speed": 0.9}, part="hook", tier="hero")
    assert [LIB[s]["reference"]["class"] for s, _w in hook] == ["whoosh", "impact"]


def test_a_palette_takes_turns_and_never_repeats_inside_the_gap():
    st = _st()
    got = []
    for i in range(9):
        lay, _ = _layers({"kind": "move", "f": 30 * (2 + 1.2 * i), "travel_s": 0.12, "speed": 0.9}, t=2 + 1.2 * i, st=st)
        got.append((2 + 1.2 * i, lay[-1][0]))
    sids = [s for _t, s in got]
    assert len(set(sids)) <= SCR.PALETTE["whoosh"]
    assert all(sids[i] != sids[i + 1] for i in range(len(sids) - 1))
    for i, (t, s) in enumerate(got):
        assert not any(s2 == s and 0 < t - u < SCR.SAME_GAP_S for u, s2 in got[:i])


def _l1():
    blur = [11.0, 6.0, 2.5] + [0.0] * 27
    crop_still = [[0.3, 0.0, 0.6, 1.0]] * 30
    crop_move = [[0.3, 0.0, 0.6, 1.0]] * 10 + [[0.3 + 0.02 * i, 0.0, 0.6 + 0.02 * i, 1.0] for i in range(1, 8)] + \
        [[0.44, 0.0, 0.74, 1.0]] * 13
    items = [dict(id="take", k="shot", pic="main", f=[0, 30], src=[0, 1], crop=crop_still),
             dict(id="plain", k="shot", pic="v01", f=[30, 60], src=[0, 1], crop=crop_still, moving=True),
             dict(id="whip", k="shot", pic="v02", f=[60, 90], src=[0, 1], crop=crop_still, blur=blur),
             dict(id="flashcut", k="shot", pic="v03", f=[90, 120], src=[0, 1], crop=crop_still),
             dict(id="fl", k="flash", f=[90, 92], mode="fill"),
             dict(id="mover", k="shot", pic="v04", f=[120, 150], src=[0, 1], crop=crop_move),
             dict(id="photo", k="card", pic="p01", f=[150, 200], x=0.5, y=0.5, w=0.6, h=0.4, s=[1.3, 1.12, 1.03, 1.0, 1.0, 1.0]),
             dict(id="slide", k="card", pic="plate", f=[210, 260], x=[0.2, 0.3, 0.38, 0.45, 0.5, 0.5, 0.5], y=0.5, w=0.3, h=0.3)]
    return dict(fps=30, dur_f=300, assets={"main": {"kind": "video"}, "v01": {"kind": "video"}, "v02": {"kind": "video"},
                                          "v03": {"kind": "video"}, "v04": {"kind": "video"}, "p01": {"kind": "image"}}, items=items)


def test_annotate_reads_how_each_picture_arrives():
    l1 = _l1()
    ev = {e["id"]: e for e in SCR.annotate(SD.l1_events(l1), l1)}
    assert ev["whip"].get("whip") and ev["whip"]["travel_s"] <= 0.15 and ev["whip"]["speed"] > 0.8
    assert ev["flashcut"].get("flash_cut") and SD.motivated(ev["flashcut"])
    assert ev["photo"].get("still") and ev["photo"].get("slam")
    assert ev["slide"].get("travel_s") and not ev["slide"].get("slam")
    move = next(e for e in ev.values() if e["kind"] == "move")
    assert 0.15 <= move["travel_s"] <= 0.4


def test_restraint_holds_a_bright_sound_under_the_voice_top_band():
    rate = 16000
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(1)
    voice = (0.1 * np.sin(2 * np.pi * 220 * t) + 0.02 * np.sin(2 * np.pi * 3100 * t) +
             0.003 * rng.standard_normal(len(t))).astype(np.float32)
    ref = SCR.voice_hf(voice, rate)
    sounds = [{"id": "a", "s": "c_ui", "db": -5.0, "tier": "tick"}, {"id": "b", "s": "c_ui", "db": -60.0, "tier": "tick"},
              {"id": "c", "s": "i_deep", "db": -2.0, "tier": "hero"}]
    rep = {}
    out = SCR.restrain([dict(s) for s in sounds], voice, rate, 0.0, LIB, rep)
    assert LIB["c_ui"]["screen"]["hf_db"] + out[0]["db"] <= ref + SCR.HF_OVER["tick"] + 0.06
    assert out[1]["db"] == -60.0                                   # already under: untouched
    assert rep["screen"]["restrained"] and rep["screen"]["restrained"][0]["id"] == "a"


def _events():
    l1 = _l1()
    return l1, SCR.annotate(SD.l1_events(l1), l1)


def test_screen_placement_end_to_end_sounds_only_what_the_picture_does():
    """On the product library (no set measures): a plain cut never sounds, every sound sits on a motivated event's frame."""
    l1, ev = _events()
    ev = [dict(e, moving=None) if e.get("id") == "plain" else e for e in ev]       # the workflow no longer marks subject motion
    ev += [{"f": 270, "kind": "display", "id": "w1"}, {"f": 285, "kind": "display", "id": "w2"}]
    ctx = {"fps": 30, "dur_f": 300, "existing": [], "speech": None, "voice": None, "words": [(0.2, 9.0)], "bed": True,
           "np10": 6.0, "seed": SD.film_seed("screen"), "screen": True, "tick_10s": 1.5}
    sounds, rep = SD.plan_sounds(ev, ctx)
    frames = {e["f"] for e in ev if SD.motivated(e)}
    assert sounds and all(s["event_f"] in frames for s in sounds)
    assert not any(s["event_f"] == 30 for s in sounds)                           # the plain cut to a clip
    lib = SD.library()
    excluded = SD.excluded()
    assert not any(s["s"] in excluded for s in sounds)
    disp = {s["s"] for s in sounds if s.get("for") == "display"}
    assert len(disp) <= SCR.PALETTE["click"]
    assert all(s["s"] in lib for s in sounds)


def test_customers_keep_their_placement():
    """Without the switch the plan is exactly the product's: the same sounds as before for the same events."""
    l1, _ = _events()
    ev = SD.l1_events(l1)
    ctx = {"fps": 30, "dur_f": 300, "existing": [], "speech": None, "voice": None, "words": [(0.2, 9.0)], "bed": True,
           "np10": 6.0, "seed": SD.film_seed("customer")}
    a, ra = SD.plan_sounds([dict(e) for e in ev], dict(ctx))
    b, rb = SD.plan_sounds([dict(e) for e in ev], dict(ctx, screen=False))
    assert [(s["s"], s["event_f"], s["db"]) for s in a] == [(s["s"], s["event_f"], s["db"]) for s in b]
    assert "screen" not in ra


def test_a_try_the_voice_hid_never_joins_the_palette():
    st = _st()
    first, _ = _layers({"kind": "move", "f": 150, "travel_s": 0.2, "speed": 0.6}, t=5.0, st=st)
    hidden = first[-1][0]
    again, _ = SCR.layers(dict({"kind": "move", "f": 150, "travel_s": 0.2, "speed": 0.6}, _w=0.5), "plain", "body", 5.0, st, LIB, _pre,
                          avoid={hidden})
    assert again[-1][0] != hidden
    assert hidden not in st["palette"]["whoosh"] and all(s != hidden for _u, s in st["heard"])
