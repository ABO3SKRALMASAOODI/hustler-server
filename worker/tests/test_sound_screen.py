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
    # every sound sits on a motivated event's frame, or is the hook's, the turn's or the close's landing (lane
    # sfx-moments: a new picture on the story's beat triggers its hit, plain cut or not)
    assert sounds and all(s["event_f"] in frames or s.get("part") in ("hook", "payoff", "close") for s in sounds)
    assert not any(s["event_f"] == 30 and s.get("part") not in ("hook", "payoff", "close") for s in sounds)     # a plain cut
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


# ----------------------------------------------------------------- review round (20 Nov): measured travel, rotation, phone, objects

def _landing_l1(crop=None, blur=None):
    """A stories-style clip whose own inner cut is a landing flagged `moving` (score.sound.landing_events): reel.py
    marks a take moving by its subject's motion. No camera move, no blur, no flash at the inner cut."""
    still = [[0.2, 0.0, 0.8, 1.0]] * 60
    shot = dict(id="v20", k="shot", pic="v20", f=[30, 90], src=[0.0, 2.0], crop=crop or still)
    if blur is not None:
        shot["blur"] = blur
    return dict(fps=30, dur_f=150, assets={"main": {"kind": "video"}, "v20": {"kind": "video", "path": "v20.mp4",
                                                                            "landings": [{"t": 0.8, "kind": "cut", "moving": True}]}},
                items=[dict(id="take", k="shot", pic="main", f=[0, 30], src=[0, 1], crop=[[0.2, 0.0, 0.8, 1.0]] * 30), shot,
                       dict(id="take2", k="shot", pic="main", f=[90, 150], src=[1, 3], crop=[[0.2, 0.0, 0.8, 1.0]] * 60)])


def test_an_inner_cut_flagged_moving_without_measured_travel_never_sounds():
    l1 = _landing_l1()
    raw = SD.l1_events(l1)
    inner = next(e for e in raw if e.get("landing"))
    assert inner["f"] == 54 and inner["moving"]                    # the flag the stories path writes
    ev = {e["f"]: e for e in SCR.annotate(raw, l1)}
    e = ev[54]
    assert not e["moving"] and not SCR.travels(e) and not SD.motivated(e)
    lay, cls = _layers(dict(e, moving=True))                       # even handed the bare flag, the screen does not sweep
    assert cls != "whoosh"
    ctx = {"fps": 30, "dur_f": 150, "existing": [], "speech": None, "voice": None, "words": [(0.2, 4.5)], "bed": True,
           "np10": 6.0, "seed": SD.film_seed("landing"), "screen": True, "tick_10s": 1.5}
    sounds, _rep = SD.plan_sounds(list(ev.values()), ctx)
    assert not any(abs(s["event_f"] - 54) <= 2 for s in sounds)


def test_an_inner_cut_with_its_own_whip_still_travels():
    blur = [0.0] * 24 + [10.0, 6.0, 2.0] + [0.0] * 33             # blurred at the inner cut's own frames (54-56)
    l1 = _landing_l1(blur=blur)
    ev = {e["f"]: e for e in SCR.annotate(SD.l1_events(l1), l1)}
    assert ev[54].get("whip") and ev[54]["moving"] and SCR.travels(ev[54])


def test_the_palette_rotates_its_sweeps_like_an_editor():
    """The m06 re-run at 0c4890b5 played one whip four times in 25 s: similar moves must not all take the same voice."""
    st = _st()
    got = []
    for i in range(6):                                              # six like moves in 25 s, as on m06
        t = 1.0 + 4.6 * i
        lay, _ = _layers({"kind": "move", "f": int(30 * t), "travel_s": 0.13, "speed": 0.95}, t=t, st=st)
        got.append((t, lay[-1][0]))
    sids = [s for _t, s in got]
    assert len(set(sids)) >= 2 and all(sids[i] != sids[i + 1] for i in range(len(sids) - 1))
    for t, s in got:
        assert sum(1 for u, s2 in got if s2 == s and t - SCR.TURN_WINDOW_S < u <= t) <= SCR.TURN_MOST


def test_a_type_voice_stays_one_voice():
    st = _st()
    voices = {(_layers({"kind": "display", "f": 30 * (2 + 3 * i)}, tier="tick", t=2.0 + 3 * i, st=st)[0] or [[None]])[0][0]
              for i in range(8)}
    assert len(voices) <= SCR.PALETTE["click"]


def test_a_hero_impact_is_one_a_phone_plays():
    lib = dict(LIB)
    lib["i_sub"] = dict(_row("i_sub", "impact", 0.8, c=59, low=1.0))
    lib["i_sub"]["screen"].update(body_db=-25.3, trans_db=-18.8)
    lib["i_body"] = dict(_row("i_body", "impact", 0.9, c=300, low=0.6))
    lib["i_body"]["screen"].update(body_db=-4.0, trans_db=-3.5)
    for k in ("i_deep", "i_mid", "i_whoosh_in"):
        lib[k] = dict(lib[k], screen=dict(lib[k]["screen"], body_db=-9.0, trans_db=-14.0))
    st = {"desc": {k: {"kind": v["reference"]["class"], "family": v.get("family")} for k, v in lib.items()}, "seed": 3, "bed": True}
    for part in ("hook", "payoff", "close"):
        got = SCR.pick("impact", dict(st, palette={}, heard=[]), lib, 5.0, want_s=0.8, low=part != "hook", transient=part == "hook")
        assert got == "i_body", (part, got)
    assert SCR.audible(lib["i_sub"]) < -20 and SCR.audible(lib["i_sub"], transient=True) < -15


def test_a_glitched_flash_and_typed_text_are_read_from_the_plan():
    items = [dict(id="take", k="shot", pic="main", f=[0, 60], src=[0, 2], crop=[[0.2, 0.0, 0.8, 1.0]] * 60),
             dict(id="b", k="shot", pic="v1", f=[60, 120], src=[0, 2], crop=[[0.2, 0.0, 0.8, 1.0]] * 60),
             dict(id="g1", k="flash", f=[60, 61], mode="neg", a=1.0, rect=[0.3, 0.4, 0.42, 0.47]),
             dict(id="g2", k="flash", f=[62, 64], mode="neg", a=1.0, rect=[0.3, 0.4, 0.42, 0.47]),
             dict(id="white", k="flash", f=[30, 32], mode="fill", c="#FFFFFF", a=1.0)]
    items += [dict(id=f"s.0.{i:03d}", k="word", f=[90 + i, 140], t=ch, v="s", x=0.1 + 0.03 * i, y=0.5) for i, ch in enumerate("Cut it")
              if ch != " "]
    l1 = dict(fps=30, dur_f=150, assets={"main": {"kind": "video"}, "v1": {"kind": "video"}}, items=items)
    ev = SCR.annotate(SD.l1_events(l1), l1)
    at = {e["f"]: e for e in ev}
    assert at[60].get("glitch") and "glitch" in SCR.fits(at[60])
    assert not any(e.get("glitch") for e in ev if e["f"] != 60)         # a white frame is not a glitch
    typed = [e for e in ev if e.get("typed")]
    assert len(typed) == 1 and typed[0]["f"] == 90 and 0.15 <= typed[0]["type_s"] <= 0.25
    lay, _ = _layers(at[60])
    assert LIB[lay[0][0]].get("family") == "glitch"
    lib = dict(LIB, keys_a=dict(_row("keys_a", "click", 2.4, family="type"), align="start"))
    st = {"desc": {k: {"kind": v["reference"]["class"], "family": v.get("family")} for k, v in lib.items()}, "seed": 1, "bed": True}
    lay, _ = SCR.layers(dict(typed[0], _w=0.5), "plain", "body", 3.0, st, lib, _pre)
    assert lay and lay[0][0] == "keys_a"
    plain, _ = SCR.layers(dict(kind="display", f=90, _w=0.5), "plain", "body", 3.0, dict(st, palette={}, heard=[]), lib, _pre)
    assert plain and plain[0][0] != "keys_a"                            # a bed only under type being typed


def _set_of(tmp_path, ids):
    """A workflow set (VALMERA_SOUNDS) holding copies of these product recordings, same ids, files and shas."""
    import json
    import shutil
    lib = os.path.join(os.path.dirname(HERE), "sound_library")
    man = {r["id"]: r for r in json.load(open(os.path.join(lib, "manifest.json")))}
    d = tmp_path / "set"
    d.mkdir()
    rows = []
    for i in ids:
        shutil.copy(os.path.join(lib, man[i]["file"]), d / man[i]["file"])
        rows.append(man[i])
    (d / "manifest.json").write_text(json.dumps(rows))
    return str(d), man


def test_a_plan_from_before_the_switch_to_the_set_still_plays(tmp_path, monkeypatch):
    """Resume: an L1 designed on the product library, rendered with VALMERA_SOUNDS set to the set. A flagged id
    (impact_1) is replaced by its nearest recording the set holds; an unflagged id the set lacks (whoosh_9) plays from
    the product library. Nothing is skipped and nothing crashes (sfx_filter.swap read a None row)."""
    import shutil
    import importlib.util
    from types import SimpleNamespace
    import pytest
    ff = os.environ.get("FFMPEG_BIN", "ffmpeg")
    if shutil.which(ff) is None:
        pytest.skip("needs ffmpeg")
    spec = importlib.util.spec_from_file_location("sfx_filter_t", os.path.join(os.path.dirname(HERE), "sfx_filter.py"))
    SF = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SF)
    rep_id = SF.ranking("impact_1")[0][1]
    set_dir, man = _set_of(tmp_path, [rep_id, "whoosh_soft_1"])
    assert SF.is_flagged("impact_1") and not SF.is_flagged("whoosh_9")
    item = dict(id="a", s="impact_1", file="impact_1.flac", event_f=60, start_s=1.5, trim_s=0.0, db=-8.0, peak_s=man["impact_1"]["peak_s"])
    out, ch = SF.swap(dict(item), set_dir)
    assert ch and out["s"] == rep_id and os.path.exists(os.path.join(set_dir, out["file"]))
    assert abs(out["start_s"] - out["trim_s"] + out["peak_s"] - (1.5 + man["impact_1"]["peak_s"])) < 1e-3
    out2, ch2 = SF.swap(dict(item, peak_s=None), set_dir)            # an item measured nowhere still swaps
    assert ch2 and out2["s"] == rep_id
    from scene import audio as AU
    monkeypatch.setattr(AU, "SOUNDS", set_dir)
    sounds = [item, dict(id="b", s="whoosh_9", file="whoosh_9.flac", event_f=120, start_s=3.6, trim_s=0.0, db=-12.0,
                         peak_s=man["whoosh_9"]["peak_s"])]
    plan = SimpleNamespace(dur=150, fps=30, doc={"spine": [], "sound": sounds}, assets={})
    _voice, fx, rep = AU.split_audio(plan)
    assert rep["skipped"] == [] and rep["sounds"] == 2
    assert [r["id"] for r in rep.get("replaced") or []] == ["impact_1"]
    assert float(np.abs(np.asarray(fx)[int(3.5 * AU.SR):int(4.5 * AU.SR)]).max()) > 1e-3     # whoosh_9 is heard


def test_the_band_limit_is_the_final_level_over_the_song(tmp_path, monkeypatch):
    """present_over lifts a sound for the bed (TIER_LIFT); under screen placement the 2-8 kHz restraint holds after it."""
    rate = SD.AR
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(2)
    voice = (0.1 * np.sin(2 * np.pi * 220 * t) + 0.004 * rng.standard_normal(len(t))).astype(np.float32)
    lib = {"c_ui": dict(LIB["c_ui"], hit_lufs=-14.0)}
    monkeypatch.setattr(SD, "library", lambda: lib)
    sounds = [{"id": "a", "s": "c_ui", "db": 2.0, "tier": "tick"}]
    monkeypatch.delenv(SCR.MODE_ENV, raising=False)
    assert SD.screen_restrain([dict(s) for s in sounds], voice)[0]["db"] == 2.0      # a customer's film: as it was
    monkeypatch.setenv(SCR.MODE_ENV, "screen")
    out = SD.screen_restrain([dict(s) for s in sounds], voice)
    ref = SCR.voice_hf(voice, rate)
    assert out[0]["db"] < 2.0 and LIB["c_ui"]["screen"]["hf_db"] + out[0]["db"] <= ref + SCR.HF_OVER["tick"] + 0.06


def test_present_over_ends_on_the_band_limit(monkeypatch):
    rate = SD.AR
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(3)
    voice = (0.1 * np.sin(2 * np.pi * 220 * t) + 0.004 * rng.standard_normal(len(t))).astype(np.float32)
    bed = (0.05 * rng.standard_normal(len(t))).astype(np.float32)
    lib = {"c_ui": dict(LIB["c_ui"], hit_lufs=-14.0)}
    monkeypatch.setattr(SD, "library", lambda: lib)
    monkeypatch.setattr(SD, "_presence", lambda it, b: 0.0)          # the song covers it: present_over lifts it fully
    monkeypatch.setattr(SD, "tier_ceiling", lambda sid, ref, tier: 30.0)
    monkeypatch.setattr(SD, "_masking", lambda *a, **k: 0.0)
    item = {"id": "a", "s": "c_ui", "db": -10.0, "tier": "hero", "layer": "hit", "start_s": 1.0, "event_f": 30}
    monkeypatch.delenv(SCR.MODE_ENV, raising=False)
    lifted = SD.present_over([dict(item)], voice, bed, 30)[0]["db"]
    monkeypatch.setenv(SCR.MODE_ENV, "screen")
    held = SD.present_over([dict(item)], voice, bed, 30)
    cap = SCR.voice_hf(voice, rate) + SCR.HF_OVER["hero"]
    assert lifted == -10.0 + SD.TIER_LIFT["hero"]                    # the lift itself is unchanged
    assert LIB["c_ui"]["screen"]["hf_db"] + lifted > cap + 1.0         # it took the sound over the band limit
    assert LIB["c_ui"]["screen"]["hf_db"] + held[0]["db"] <= cap + 0.06
