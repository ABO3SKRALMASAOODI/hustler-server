"""suggest_devices on the REAL Wave 1 cards (integration review, w1r).

The 1F planner tests run on stand-in cards (tests/fixtures/w1f_cards). On
the cards the five device tracks shipped, the planner proposed every
sustained device as a 2-frame flicker (a grey world for 0.07 s, a split
tone, a poster, a CRT on a jump cut, a word behind the head for two
frames), put composites and finishes first by the alphabet, set a ghost
stroke on graphics nothing hides, a light blend on the hook and a
difference blend on the payoff, and laid a white silhouette under a white
payoff lockup. These pin the planner's half of the taste
(device_planner.CARD_TASTE) on the real cards.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import device_planner as dp  # noqa: E402
import devices  # noqa: E402
import schemas  # noqa: E402


def _program(shots=((0.0, 14.5), (20.0, 40.0)), keep=None):
    text = ("We tried everything in the lab for years. Nothing worked on "
            "the screen. Everyone said the idea was dead. | But then the "
            "numbers changed. We did not quit. The engine runs hot now. "
            "Today it costs 140 dollars.")
    words, t = [], 0.4
    for tok in text.split():
        if tok == "|":
            t = 20.4
            continue
        dur = 0.32 if len(tok) > 4 else 0.22
        words.append({"w": tok, "t0": round(t, 2), "t1": round(t + dur, 2),
                      "p": 0.95})
        t += dur + (0.45 if tok.endswith(".") else 0.06)
    index = {"video": {"duration": 40.0, "width": 1920, "height": 1080,
                       "fps": 30.0, "has_audio": True}, "words": words,
             "shots": [{"start": a, "end": b} for a, b in shots]}
    edl = schemas.validate_edl({"keep": keep or [[0.0, 14.5], [20.0, 37.0]]},
                               40.0).model_dump()
    return edl, index


def _suggest(edl, index, **kw):
    kw.setdefault("matte", {"status": "ready", "person": True})
    return dp.suggest(edl, index, duration=40.0, **kw)


def _win(c):
    v = c["op"]["value"]
    return v.get("start"), v.get("end")


SUSTAINED = {"isolate", "poster", "glow", "outline", "exposure", "echo"}


def test_real_cards_are_proposed_with_windows_their_device_holds():
    edl, index = _program()
    seen = set()
    for level in ("designed", "charged"):
        for c in _suggest(edl, index, intensity=level, max_n=8):
            v = c["op"]["value"]
            seen.add(c["device"])
            a, b = _win(c)
            if v.get("type") == "subject" and v.get("kind") in SUSTAINED:
                assert b - a >= 0.75, (c["device"], a, b)
            if v.get("type") == "grade_window":
                assert b - a >= dp.SETUP_MIN_S - 1e-6, (c["device"], a, b)
            if v.get("type") == "finish" and v.get("kind") not in (
                    "band_tear", "pixel_stretch"):
                assert b - a >= 0.5, (c["device"], a, b)
            if c["family"] == "type_depth":
                assert b - a >= 1.0, (c["device"], a, b)
    assert seen, "the real cards proposed nothing"


def test_a_grade_section_ends_on_the_turn_it_lands_on():
    edl, index = _program()
    cards = devices.load_cards()
    card, _p, _g, _h = dp.card_taste(cards["grade_section"])
    plan = dp.moments(edl, index)
    turn = next(m for m in plan["moments"]
                if m.get("word") == "but" and m["kind"] in ("word", "stress"))
    op, item, _at = dp.build_op(card, turn, plan, item_id="gs1",
                                fps=plan["fps"], edl=edl)
    assert op is not None
    assert item["end"] == pytest.approx(dp.time_of(
        dp.frame_of(turn["t"], plan["fps"]), plan["fps"]))
    assert item["end"] - item["start"] >= dp.SETUP_MIN_S
    assert item["saturation"] == 0.0 and item["ramp_out_f"] == 3


def test_finishes_need_their_metaphor_and_glitches_take_cuts():
    plan = {"words": [{"w": "engine", "t0": 5.0}, {"w": "today", "t0": 9.0}]}
    hot = {"t": 5.0, "kind": "word", "word": "engine"}
    cold = {"t": 9.0, "kind": "word", "word": "today"}
    assert dp.metaphor_ok("heat", hot, plan)
    assert not dp.metaphor_ok("heat", cold, plan)
    # a word moment carries the metaphor itself (the device lands ON it);
    # a cut takes the words said just after it
    assert not dp.metaphor_ok("heat", {"t": 4.2, "kind": "stress",
                                       "word": "the"}, plan)
    assert dp.metaphor_ok("heat", {"t": 4.2, "kind": "cut", "word": None},
                          plan)
    assert dp.metaphor_ok("glitch", {"t": 7.0, "kind": "cut", "word": None},
                          plan)
    assert not dp.metaphor_ok("screen", hot, plan)
    edl, index = _program()
    for c in _suggest(edl, index, intensity="charged"):
        v = c["op"]["value"]
        if v.get("type") == "finish":
            gate = dp.CARD_TASTE[c["device"]]["gate"]
            assert gate in ("heat", "screen", "glitch"), (c["device"], gate)


def test_composites_modify_only_the_graphics_they_are_for():
    edl, index = _program()
    edl["motion"] = [
        {"id": "hook", "template": "word_slam", "start": 0.0, "end": 2.0,
         "layer": "above_captions",
         "params": {"text": "THE LAB", "tier": "hook"}},
        {"id": "payoff", "template": "word_slam", "start": 27.0,
         "end": 29.5, "layer": "above_captions",
         "params": {"text": "140 DOLLARS", "tier": "payoff"}}]
    edl = schemas.validate_edl(edl, 40.0).model_dump()
    for c in _suggest(edl, index, intensity="charged", max_n=8):
        if c["op"]["layer"] != "motion" or c["family"] != "composite":
            continue
        assert c["op"]["id"] not in ("hook", "payoff"), c
    assert dp.host_ok("behind", {"layer": "behind_subject"})
    assert not dp.host_ok("behind", {"layer": "above_captions"})
    assert dp.host_ok("light", {"template": "light_leak"})
    assert not dp.host_ok("light", {"template": "word_slam"})
    assert not dp.host_ok("type", {"id": "payoff", "template": "word_slam",
                                   "params": {"text": "140"}})
    assert dp.host_ok("type", {"id": "m3", "template": "word_slam",
                               "params": {"text": "ENOUGH"}})
    # the cards that need the editor's footage, region or file never come
    # automatically
    for cid in ("composite_fill", "composite_window", "region_tone",
                "region_material", "grade_lut_import", "strobe_frames"):
        assert dp.card_taste(devices.load_cards()[cid])[1] == 0.0


def test_jump_cuts_inside_one_take_stay_hard():
    # one camera: both kept spans sit in the same indexed shot
    edl, index = _program(shots=((0.0, 40.0),))
    cuts = dp.program_cuts(edl, index)
    assert cuts and not cuts[0]["shot_change"]
    for c in _suggest(edl, index, intensity="charged"):
        assert c["family"] != "transition", c["device"]
    # a real camera change takes a transition (once per cap)
    edl, index = _program()
    cuts = dp.program_cuts(edl, index)
    assert cuts[0]["shot_change"]
    juncs = [c for c in _suggest(edl, index, intensity="charged")
             if c["family"] == "transition"]
    assert len(juncs) <= dp.TRANSITION_CAP["charged"]


def test_no_flash_in_the_hooks_first_second():
    edl, index = _program()
    for level in ("designed", "charged"):
        for c in _suggest(edl, index, intensity=level):
            if (c.get("owner_flags") or {}).get("flash"):
                assert c["at"] >= dp.HOOK_FLASH_FREE_S, c


def test_a_flat_colour_under_light_type_keeps_the_type_readable():
    edl = {"motion": [{"id": "payoff", "template": "word_slam",
                       "start": 10.0, "end": 12.0,
                       "params": {"text": "*140* / characters",
                                  "color": "#F8F6F2",
                                  "accent": "#FFC940"}}],
           "captions": {"style": {"highlight_color": "#FFC940"}}}
    assert dp.ink_safe(edl, 10.0, 10.2, ["white", "accent", "black"]) \
        == "black"
    assert dp.ink_safe(edl, 13.0, 13.2, ["white", "black"]) == "white"
    dark = {"motion": [dict(edl["motion"][0], params={
        "text": "140", "color": "#111114"})]}
    assert dp.ink_safe(dark, 10.0, 10.2, ["white", "black"]) == "white"
    import recipes
    plan = {"program_s": 20.0, "fps": 30.0, "sections": [(0.0, 20.0)],
            "moments": [{"t": 10.0, "kind": "stress", "strength": 1.0,
                         "word": "140", "bind": {"on": "time", "ref": 10.0}}],
            "payoff": {"t": 10.0, "moment": {
                "t": 10.0, "kind": "stress", "strength": 1.0, "word": "140",
                "bind": {"on": "time", "ref": 10.0}}, "hold_end": 11.5}}
    items, _m, why = recipes.build("white_silhouette", plan, edl=edl)
    assert items[0]["colour"] == "black" and "black" in why
    items, _m, _why = recipes.build("white_silhouette", plan)
    assert items[0]["colour"] == "white"


def test_sustained_devices_never_stack_and_hits_may_land_inside():
    for level in ("designed", "charged"):
        edl, index = _program()
        got = [c["op"]["value"] for c in _suggest(edl, index,
                                                  intensity=level)
               if c["op"]["layer"] == "fx" and c["op"]["value"].get("end")]
        longs = [(v["start"], v["end"]) for v in got
                 if v["end"] - v["start"] >= dp.SUSTAINED_S]
        for i, (a, b) in enumerate(longs):
            for c, d in longs[i + 1:]:
                assert not (a < d and c < b), (level, longs)
    hit = {"start": 5.0, "end": 5.07}
    grey = {"start": 2.0, "end": 8.0}
    chosen = [{"device": "grade_section", "op": {"layer": "fx",
                                                 "value": grey}}]
    assert not dp._overlaps_same(hit, "subject_fill_flash", chosen, [])
    assert dp._overlaps_same({"start": 6.0, "end": 9.0}, "subject_poster",
                             chosen, [])


def test_a_batch_is_steered_off_the_devices_its_siblings_carry():
    """Freshness for devices too (recipes have it): with the alphabet as
    the tie-break every short of a batch opened its scene change with
    'junction_bands'. Sibling usage decays a card's rank; ties fall in a
    per-project order; one transition style per short."""
    edl, index = _program()
    first = _suggest(edl, index, intensity="charged", seed=1)
    top = first[0]["device"]
    sib = [{"fx": [{"id": "a", "device": top}]},
           {"fx": [{"id": "b", "device": top}]}]
    usage = dp.batch_usage(sib)
    assert usage == {top: 2}
    again = _suggest(edl, index, intensity="charged", seed=1, batch=usage)
    assert again[0]["device"] != top
    styles = {c["device"] for c in again if c["family"] == "transition"}
    assert len(styles) <= 1
    juncs = {s: [c["device"] for c in _suggest(edl, index, seed=s)
                 if c["family"] == "transition"] for s in range(1, 9)}
    assert len({tuple(v) for v in juncs.values()}) > 1, juncs


def test_the_word_behind_the_head_is_one_hero_at_head_height():
    """On the real cards the planner's word behind the speaker sat at
    word_slam's default y 0.56 (behind the chest, hidden by a dark shirt),
    twice a short, and on the payoff it set the payoff lockup's words a
    second time behind the head (review w1r, Thiel '140 CHARACTERS')."""
    edl, index = _program()
    edl["motion"] = [{"id": "payoff", "template": "word_slam", "start": 21.2,
                      "end": 23.0, "layer": "above_captions",
                      "params": {"text": "140 DOLLARS", "tier": "payoff"}}]
    edl = schemas.validate_edl(edl, 40.0).model_dump()
    for level in ("designed", "charged"):
        heroes = [c["op"]["value"] for c in _suggest(edl, index,
                                                     intensity=level)
                  if c["family"] == "type_depth"]
        assert len(heroes) <= 1
        for v in heroes:
            assert v["params"]["tier"] == "hero"
            assert v["params"]["y"] <= 0.3
            assert not (v["start"] < 23.0 and 21.2 < v["end"]), v
