"""The editing doctrine teaches the premium short-form grammar.

Valmera's quality ceiling was policy: prompts and skills defaulted to
restraint, silence and static type while the owner's reference reels show
committed art direction, motion typography, sound edited to picture and eased
camera moves. These tests pin the measurable grammar and keep the retired
restraint defaults from creeping back.
"""

import agent_prompt
import agent_tools


def _skill(name):
    text = agent_prompt.read_skill_text(name)
    assert text, name
    return text


def test_core_prompt_states_the_premium_short_form_standard():
    p = agent_prompt.CORE_PROMPT
    assert "PREMIUM SHORT-FORM IS THE STANDARD FOR REELS" in p
    for phrase in ("0.1-0.6 s", "hook line as text by 1.5 s",
                   "every 0.3-0.6 s", "2-4 hero moments",
                   "13-20 dB under the voice", "landing zooms after jump cuts",
                   "never a whoosh on every caption",
                   "deliberate choice for that passage",
                   "Never invent numbers"):
        assert phrase in p, phrase
    # The workflow names playbooks that exist.
    for name in ("short-form-direction", "motion-design"):
        assert name in p and name in agent_prompt.skill_names()


def test_in_app_tool_paging_is_one_strippable_sentence():
    s = agent_prompt.IN_APP_TOOL_PAGING
    assert s in agent_prompt.CORE_PROMPT
    # backend/routes/mcp.py removes it by these anchors for MCP clients.
    assert s.startswith("The first provider page intentionally carries")
    assert s.endswith("rest of the turn.") and s.count(".") == 1
    assert "load_tools" in s


def test_skills_no_longer_default_to_restraint_and_silence():
    retired_defaults = (
        "zero zooms is a finished", "zero zooms is often",
        "hard cut is the default, including between real scenes",
        "restraint often is the look", "restraint is the look",
        "restraint reads expensive", "no decoration quota",
        "no added music", "no stupid punch-ins",
        "zero authored transitions is a complete treatment")
    for name in agent_prompt.skill_names():
        text = _skill(name).lower()
        for phrase in retired_defaults:
            assert phrase not in text, (name, phrase)


def test_short_form_direction_carries_the_measurable_grammar():
    text = _skill("short-form-direction")
    for phrase in ("apply_look", "0.1–0.6 s", "by 1.5 s", "0.3–0.6 s",
                   "2–4 hero moments", "motion_look", "landing zooms",
                   "13–20 dB", "never a small card on a flat black void",
                   "comment_cta", "Never invent facts"):
        assert phrase.lower() in text.lower(), phrase


def test_motion_design_skill_teaches_templates_timing_runtime_and_review():
    text = _skill("motion-design")
    for phrase in ("list_motion_templates()", "get_kept_transcript()",
                   "above_captions", "below_captions", "behind_subject",
                   "One leader per moment", "ghost-snap", "strobe",
                   "rise-blur", "typewriter", "springs",
                   "MG.frame", "MG.active", "MG.box", "MG.spring",
                   "MG.tween", "MG.split", "MG.fit", "MG.count", "MG.draw",
                   "Math.random", "render_preview(complete=false)",
                   "look_at(rendered=true"):
        assert phrase in text, phrase


def test_audio_teaches_the_kit_and_cue_timing():
    text = _skill("audio")
    for phrase in ("list_sfx_kit()", "kit:<kind>", "Pre-roll",
                   "riser_short", "sub_drop", "Never a whoosh on every caption",
                   "Bed 13–20 dB under the voice"):
        assert phrase in text, phrase
    music = _skill("music")
    assert "list_music_library tool" in music
    assert "add_library_music tool" in music


def test_compact_contracts_carry_the_creative_menus():
    contracts = agent_tools._COMPACT_CONTRACTS
    for name in ("list_motion_templates", "add_motion_graphic",
                 "set_motion_graphic", "list_sfx_kit", "add_sfx",
                 "add_captions", "set_caption_style", "apply_look",
                 "add_zoom", "punch_in_on_emphasis", "set_transitions",
                 "set_picture_card", "look_at"):
        assert name in contracts, name
    assert "kit:<kind>" in contracts["add_sfx"]
    assert "motion_look" in contracts["add_captions"]
    for look in ("editorial", "creator_punch", "cinematic_doc", "mono_noir",
                 "clean_minimal"):
        assert look in contracts["apply_look"]
    for mode in ("punch", "landing", "push", "pulse"):
        assert mode in contracts["add_zoom"]
    assert "behind_subject" in contracts["add_motion_graphic"]
    # look_at works without a render for source and assembled geometry.
    assert "no render needed" in contracts["look_at"]
    assert "Requires render_preview" not in contracts["look_at"]


def test_compact_catalog_uses_the_contracts():
    compact = {t["function"]["name"]: t["function"]["description"]
               for t in agent_tools.openai_tools(compact=True)}
    for name, contract in agent_tools._COMPACT_CONTRACTS.items():
        if name in compact:
            assert compact[name] == contract
