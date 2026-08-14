"""Post-plan schema routing cuts repeated tokens without hiding capability."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import agent_loop                                              # noqa: E402


class _Ctx:
    user_message = "make a coherent podcast reel with clean captions"
    edit_plan = {"step_states": [
        {"task": "select and cut one complete exchange", "status": "pending"},
        {"task": "add clean captions", "status": "pending"},
        {"task": "add music", "status": "completed"},
    ]}

    def __init__(self):
        self._expanded_tool_domains = set()


def test_post_plan_catalog_is_stage_relevant_and_materially_smaller():
    ctx = _Ctx()
    names = agent_tools.compact_tool_names(ctx)
    assert {"keep_segments", "add_captions", "render_preview",
            "expand_toolset"} <= names
    assert "search_stock" not in names

    full = agent_tools.openai_tools(compact=True)
    routed = agent_tools.openai_tools(compact=True, names=names)
    full_chars = len(json.dumps(full))
    routed_chars = len(json.dumps(routed))
    assert routed_chars < full_chars * 0.6


def test_any_omitted_domain_can_be_loaded_without_changing_the_edit():
    ctx = _Ctx()
    out = agent_tools.expand_toolset(ctx, ["media", "motion"])
    names = agent_tools.compact_tool_names(ctx)

    assert "Tool domains exposed" in out
    assert {"research_broll", "generate_image", "add_zoom",
            "set_color_grade"} <= names


def test_domain_union_covers_every_registered_tool():
    covered = set(agent_tools.TOOL_CORE)
    for names in agent_tools.TOOL_DOMAINS.values():
        covered.update(names)
    assert set(agent_tools.TOOLS) <= covered


def test_visual_budget_covers_library_before_deep_sampling_and_prioritizes_upload():
    clips = [{"id": i, "storage_key": f"clips/{i}.mp4"}
             for i in range(1, 9)]
    plan, omitted = agent_loop._clip_visual_plan(
        clips, tile_budget=4, per_clip=10, priority_ids=[8],
        used_keys={"clips/1.mp4"})

    assert len(plan) == 4 and omitted == 4
    assert plan[0][0]["id"] == 8
    assert [tiles for _clip, tiles in plan] == [1, 1, 1, 1]
    # Selection spans the remaining upload order; it does not take only the
    # first three after the priority item.
    assert plan[-1][0]["id"] == 1


def test_visual_budget_distributes_extra_depth_evenly():
    clips = [{"id": i, "storage_key": f"clips/{i}.mp4"}
             for i in range(1, 5)]
    plan, omitted = agent_loop._clip_visual_plan(
        clips, tile_budget=10, per_clip=4)

    assert omitted == 0
    assert [tiles for _clip, tiles in plan] == [3, 3, 2, 2]


def test_still_image_budget_prioritizes_current_upload_then_spreads_library():
    images = [{"id": i, "storage_key": f"images/{i}.jpg"}
              for i in range(1, 9)]
    picked, omitted = agent_loop._image_visual_plan(
        images, image_budget=4, priority_ids=[8],
        used_keys={"images/1.jpg"})

    assert len(picked) == 4 and omitted == 4
    assert picked[0]["id"] == 8
    # It does not silently attach only the oldest rows after the priority
    # upload; the sample reaches the far side of the remaining library.
    assert picked[-1]["id"] == 1


def test_still_image_overflow_is_explicit_not_hidden_by_the_sql_limit():
    images = [{"id": i, "storage_key": f"images/{i}.jpg"}
              for i in range(1, 26)]
    picked, omitted = agent_loop._image_visual_plan(images, image_budget=20)
    assert len(picked) == 20
    assert omitted == 5
    assert picked[0]["id"] == 1 and picked[-1]["id"] == 25
