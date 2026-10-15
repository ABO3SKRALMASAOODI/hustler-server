"""Recipes: whole creative moves solved from the cut (Wave 1 integration).

The brute-force phase runs a mid-tier model: these pin that a recipe lands
on the word that earns it, carries taste (B&W as a device, flash-safe,
nothing in the hook or across most of the short), writes through
apply_edit_batch like any device, and that a batch of shorts is steered
away from repeating one move (freshness, not a house style).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import edit_batch  # noqa: E402
import flash_safety  # noqa: E402
import mono_policy  # noqa: E402
import recipes  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

TEXT = ("everyone thought the factory would take ten years to build but "
        "instead we did it in nineteen months and the reason is simple "
        "the machines build the machines so the cost dropped by ninety "
        "percent and that is why the price is lower now than ever")


def _index(duration=30.0):
    words, t = [], 0.4
    for i, w in enumerate(TEXT.split()):
        words.append({"w": w, "t0": round(t, 3), "t1": round(t + 0.32, 3),
                      "i": i, "p": 0.99})
        t += 0.42
    return {"words": words, "video": {"duration": duration, "fps": 25.0,
                                      "width": 1920, "height": 1080},
            "sentences": [], "shots": [], "silences": []}


def _plan():
    import device_planner
    edl = validate_edl(default_edl(30.0), 30.0).model_dump()
    edl["keep"] = [[0.0, 26.0]]
    return edl, device_planner.moments(edl, _index())


def test_every_recipe_builds_valid_items_through_apply_edit_batch():
    edl, plan = _plan()
    built = 0
    for name in recipes.RECIPES:
        items, landing, why = recipes.build(name, plan)
        if not items:
            continue
        built += 1
        ops = [{"action": "upsert", "layer": "fx", "id": it["id"],
                "value": it} for it in items]
        out = edit_batch.apply_batch(edl, ops, 30.0, set())
        assert {i["id"] for i in out["fx"]} >= {i["id"] for i in items}
        assert all(i["id"].startswith(f"rc_{name}_") for i in items)
        assert landing is not None and why
    assert built >= 5


def test_colour_lands_on_the_word_that_carries_the_turn():
    _edl, plan = _plan()
    items, landing, why = recipes.build("mono_until_turn", plan)
    gw = items[0]
    assert gw["type"] == "grade_window" and gw["saturation"] == 0.0
    assert gw["ramp_out_f"] == 3 and gw["end"] == round(landing["t"], 2)
    # the turn: a contrast word (or the stressed word carrying it)
    assert set(landing.get("tags") or ()) & {"contrast", "negation"} or \
        landing.get("kind") == "stress"
    assert landing["t"] >= 1.2
    # a device, never the look
    assert gw["end"] - gw["start"] <= recipes.MAX_MONO_SHARE * \
        plan["program_s"] + 1e-6
    edl = {"keep": [[0.0, 26.0]], "fx": items}
    assert mono_policy.whole_mono(edl) is None


def test_negative_hits_are_flash_safe_and_stay_out_of_the_hook():
    edl, plan = _plan()
    items, _landing, _why = recipes.build("negative_hits", plan)
    if not items:
        return
    assert all(i["start"] >= 1.5 for i in items)
    ts = [i["start"] for i in items]
    assert all(b - a >= recipes.MIN_HIT_GAP_S - 0.011
               for a, b in zip(ts, ts[1:]))
    out = edit_batch.apply_batch(edl, [{"action": "upsert", "layer": "fx",
                                        "id": i["id"], "value": i}
                                       for i in items], 30.0, set())
    assert flash_safety.assess(out)["peak_per_s"] <= 3


def test_the_lab_scan_is_flash_safe_unless_the_user_opted_in():
    _edl, plan = _plan()
    keys = ["media/p1/v_photo.png", "media/p1/v_negative.png",
            "media/p1/v_line.png", "media/p1/v_blueprint.png",
            "media/p1/v_thermal.png"]
    safe, _l, _w = recipes.build("lab_scan", plan, variant_keys=keys)
    hot, _l2, _w2 = recipes.build("lab_scan", plan, flash_ok=True)
    assert safe and hot
    assert all(f["hold_f"] >= 8 and not f.get("negate")
               for f in safe[0]["frames"])
    assert len({f["source"]["asset_key"] for f in safe[0]["frames"]}) == 3
    assert all(f["hold_f"] == 2 for f in hot[0]["frames"])
    # flash-safe and without its stills the scan would be three identical
    # frames of the program (no move at all): it is not offered as ops
    none, _l3, why = recipes.build("lab_scan", plan)
    assert none is None and "stills" in why
    row = next(r for r in recipes.rank(plan) if r["name"] == "lab_scan")
    assert row["items"] is None


def test_a_move_the_batch_already_used_ranks_lower():
    """Freshness, not a house style: the owner wants more and better every
    time, so a recipe the sibling shorts used ranks below a fresh one."""
    _edl, plan = _plan()
    first = recipes.rank(plan, matte_ok=True)
    top = first[0]["name"]
    siblings = [{"fx": [{"id": f"rc_{top}_1"}]},
                {"fx": [{"id": f"rc_{top}_1"}]}]
    usage = recipes.batch_usage(siblings)
    assert usage == {top: 2}
    again = recipes.rank(plan, matte_ok=True, batch=usage)
    assert again[0]["name"] != top
    row = next(r for r in again if r["name"] == top)
    assert not row["fresh"] and row["uses"] == 2
    # never twice in one short
    assert all(r["name"] != top for r in recipes.rank(
        plan, matte_ok=True, used_here={top}))


def test_matte_recipes_drop_when_no_person_is_in_shot():
    _edl, plan = _plan()
    names = {r["name"] for r in recipes.rank(plan, matte_ok=False)}
    assert not names & {n for n, r in recipes.RECIPES.items() if r.matte}


def test_the_recipe_verbs_are_reachable_and_documented():
    import agent_tools
    assert "suggest_recipes" in agent_tools.TOOLS
    assert "apply_recipe" in agent_tools.TOOLS
    enum = agent_tools.TOOLS["apply_recipe"][2]["name"]["enum"]
    assert set(enum) == set(recipes.RECIPES)
    root = os.path.dirname(os.path.dirname(HERE))
    src = open(os.path.join(root, "backend", "routes", "mcp.py")).read()
    block = src[src.index("_READ_ONLY_EXACT = {"):]
    assert '"suggest_recipes"' in block[:block.index("}")]
    json.dumps(recipes.rank(_plan()[1], matte_ok=True))   # serialisable


def test_apply_recipe_writes_once_per_short_through_the_batch_path():
    import agent_tools
    from w0c_support import tool_context
    edl = default_edl(30.0)
    edl["keep"] = [[0.0, 26.0]]
    ctx, fake = tool_context(edl, duration=30.0,
                             user_message="make it premium")
    ctx.index = _index()
    sug = agent_tools.TOOLS["suggest_recipes"][0](ctx)
    assert sug.startswith("RECIPES") and "ops:" in sug
    out = agent_tools.TOOLS["apply_recipe"][0](ctx, name="mono_until_turn")
    assert out.startswith("EDL v"), out
    assert "RECIPE mono_until_turn" in out
    fx = fake.rows[-1]["json"]["fx"]
    assert [i["id"] for i in fx] == ["rc_mono_until_turn_1"]
    again = agent_tools.TOOLS["apply_recipe"][0](ctx, name="mono_until_turn")
    assert again.startswith("REJECTED") and "once per short" in again


def test_the_white_silhouette_holds_two_frames_then_decays_three():
    """A fill's decay frames are the last decay_f frames of its window: a
    two-frame window with decay_f 3 drew one frame full and one decayed."""
    _edl, plan = _plan()
    items, _landing, _why = recipes.build("white_silhouette", plan)
    it = items[0]
    fps = plan.get("fps") or 30.0
    frames = round((it["end"] - it["start"]) * fps)
    assert frames == 2 + it["decay_f"] == 5


def test_the_payoff_the_edit_declares_is_the_payoff_devices_land_on():
    """The planner chose the loudest late figure ('26' of three stats) while
    the editor's payoff lockup (tier 'payoff') held the closing line; the
    moment model now lands "the payoff" inside the declared lockup, as the
    render's size report reads it."""
    import device_planner
    edl = validate_edl(default_edl(30.0), 30.0).model_dump()
    edl["keep"] = [[0.0, 26.0]]
    plain = device_planner.moments(edl, _index())
    edl["motion"] = [{"id": "close", "template": "word_slam", "start": 7.0,
                      "end": 9.0, "layer": "above_captions",
                      "params": {"text": "nineteen months", "tier": "payoff"}}]
    assert device_planner.declared_payoff(edl) == (7.0, 9.0)
    plan = device_planner.moments(edl, _index())
    assert 6.6 <= plan["payoff"]["t"] <= 9.0
    assert plan["payoff"]["t"] != plain["payoff"]["t"]
    items, landing, _why = recipes.build("white_silhouette", plan)
    assert 6.6 <= items[0]["start"] <= 9.0


def test_apply_recipe_at_moves_a_payoff_recipe_to_that_moment():
    """apply_recipe(at=) restricted the moments but kept the planner's
    payoff, so colour_kept_payoff still landed on the planner's word."""
    import agent_tools
    from w0c_support import tool_context
    edl = default_edl(30.0)
    edl["keep"] = [[0.0, 26.0]]
    ctx, fake = tool_context(edl, duration=30.0, user_message="premium")
    ctx.index = _index()
    out = agent_tools.TOOLS["apply_recipe"][0](ctx, name="colour_kept_payoff",
                                               at=6.3)
    assert out.startswith("EDL v"), out
    it = fake.rows[-1]["json"]["fx"][0]
    landed = it["start"] + 0.5            # the isolate opens 0.5 s early
    assert abs(landed - 6.3) <= 1.5, it
    ctx2, fake2 = tool_context(edl, duration=30.0, user_message="premium")
    ctx2.index = _index()
    agent_tools.TOOLS["apply_recipe"][0](ctx2, name="colour_kept_payoff")
    free = fake2.rows[-1]["json"]["fx"][0]
    assert abs(free["start"] + 0.5 - 6.3) > 1.5   # the planner's own payoff


def _synthetic_plan(moments, payoff_t=None, program_s=20.0):
    ms = [dict({"kind": "stress", "strength": 0.8, "tags": [],
                "bind": {"on": "time", "ref": m["t"]}, "section": 0}, **m)
          for m in moments]
    pay = None
    if payoff_t is not None:
        pm = next(m for m in ms if m["t"] == payoff_t)
        pm["payoff"] = True
        pay = {"t": payoff_t, "moment": pm, "hold_end": payoff_t + 1.2}
    return {"program_s": program_s, "fps": 30.0, "moments": ms,
            "payoff": pay, "sections": [(0.0, program_s)]}


def test_the_wider_range_recipes_land_on_their_words():
    """Review (w1r): three more moves from the same engines, each on the
    word that earns it — the room dims round the speaker on the turn, the
    world inverts round him on a hard negation, a build drains to grey and
    the colour snaps back ON the payoff."""
    plan = _synthetic_plan([
        {"t": 2.0, "word": "but", "tags": ["contrast"]},
        {"t": 5.0, "word": "never", "tags": ["negation"]},
        {"t": 8.0, "word": "ten", "tags": ["number", "payload"]},
        {"t": 9.2, "word": "twenty", "tags": ["number", "payload"]},
        {"t": 10.4, "word": "forty", "tags": ["number", "payload"]},
        {"t": 12.0, "word": "free", "tags": ["payload"], "strength": 1.0}],
        payoff_t=12.0)
    edl = validate_edl(default_edl(30.0), 30.0).model_dump()
    edl["keep"] = [[0.0, 20.0]]
    items, m, why = recipes.build("room_dims", plan)
    assert m["word"] == "but" and items[0]["scope"] == "background"
    assert items[0]["kind"] == "exposure" and items[0]["ev"] < -1
    assert items[0]["end"] - items[0]["start"] >= 1.2
    items, m, why = recipes.build("world_negative", plan)
    assert m["word"] == "never" and items[0]["scope"] == "background"
    out = edit_batch.apply_batch(edl, [{"action": "upsert", "layer": "fx",
                                        "id": i["id"], "value": i}
                                       for i in items], 30.0, set())
    assert flash_safety.assess(out)["peak_per_s"] <= 3
    items, m, why = recipes.build("colour_drain", plan)
    assert [round(i["saturation"], 2) for i in items] == [0.7, 0.42, 0.14]
    assert items[-1]["end"] == 12.0 and m["word"] == "free"
    assert [i["start"] for i in items] == [8.0, 9.2, 10.4]
    out = edit_batch.apply_batch(edl, [{"action": "upsert", "layer": "fx",
                                        "id": i["id"], "value": i}
                                       for i in items], 30.0, set())
    assert mono_policy.whole_mono(out) is None
    # no build: no drain; the negation that IS the payoff keeps its device
    plan2 = _synthetic_plan([{"t": 6.0, "word": "never",
                              "tags": ["negation"], "strength": 1.0}],
                            payoff_t=6.0)
    assert recipes.build("colour_drain", plan2)[0] is None
    assert recipes.build("world_negative", plan2)[0] is None
