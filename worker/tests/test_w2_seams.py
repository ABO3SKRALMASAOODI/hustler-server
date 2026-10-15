"""Wave 2 seams (INTERFACES_W2.md §10): departments, verbs, the fx tag,
skills, cards, the typeset seam and the B&W rule, pinned in one place."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import w2_support as S                                    # noqa: E402
import agent_tools                                        # noqa: E402
import devices                                            # noqa: E402
import schemas                                            # noqa: E402
import tools_ext                                          # noqa: E402


def test_every_wave2_verb_is_a_department_tool():
    ext = set(tools_ext.EXT_TOOL_NAMES)
    assert S.W2_VERBS <= ext, S.W2_VERBS - ext
    for dept in S.W2_DEPARTMENTS:
        assert tools_ext.EXT_DEPARTMENTS.get(dept), dept
    core = set(tools_ext.core_names(agent_tools.TOOLS))
    assert not (S.W2_VERBS & core)          # never in the core catalog


def test_department_budgets_hold():
    import json
    for dept, names in tools_ext.EXT_DEPARTMENTS.items():
        size = len(json.dumps(agent_tools.openai_tools(
            "any-model", compact=True, names=sorted(names)),
            separators=(",", ":")))
        assert size <= tools_ext.DEPARTMENT_COMPACT_BUDGET, (dept, size)


def test_arrange_is_registered_and_authorable():
    assert S.W2_FX_TAGS <= set(schemas.FX_TYPES)
    assert S.W2_FX_TAGS <= set(schemas.FX_AUTHORABLE_TYPES)


def test_every_wave2_skill_exists():
    import agent_prompt
    assert S.W2_SKILLS <= set(agent_prompt.skill_names())


def test_wave2_cards_use_their_prefixes_and_skills():
    cards = devices.load_cards()
    cards = cards if isinstance(cards, dict) else {c["id"]: c for c in cards}
    w2 = {k: v for k, v in cards.items()
          if k.split("_", 1)[0] in ("typeset", "typefx", "montage",
                                    "arrange", "ramp", "still", "recall",
                                    "story")}
    assert len(w2) >= 11
    import agent_prompt
    skills = set(agent_prompt.skill_names())
    for cid, c in w2.items():
        assert c.get("skill") in skills, cid
        assert not c["owner_flags"]["adds_sound"], cid
        assert not c["owner_flags"]["adds_zoom"], cid


def test_the_typeset_seam_solves_at_build_time():
    import motion_templates
    assert motion_templates.DERIVED.get("typeset")
    item = {"id": "t", "template": "typeset", "start": 0, "end": 2,
            "params": motion_templates.check_params(
                "typeset", {"text": "go *do* it", "voice": "magazine"})}
    extra = motion_templates.derived_params(item, 1080, 1920, 30)
    assert extra.get("_layout", {}).get("runs")


def test_bw_is_a_device_everywhere():
    import creative
    import formats
    assert agent_tools.LOOKS["mono_noir"].get("grade") != "bw"
    for p in formats.all_packs():
        assert "whole_short_mono" in p["colour_world"]["never"]
    assert creative.mono_allowed("black and white please")
    assert not creative.mono_allowed("make it cinematic")
