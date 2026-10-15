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
    safe, _l, _w = recipes.build("lab_scan", plan)
    hot, _l2, _w2 = recipes.build("lab_scan", plan, flash_ok=True)
    assert safe and hot
    assert all(f["hold_f"] >= 8 and not f.get("negate")
               for f in safe[0]["frames"])
    assert all(f["hold_f"] == 2 for f in hot[0]["frames"])


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
