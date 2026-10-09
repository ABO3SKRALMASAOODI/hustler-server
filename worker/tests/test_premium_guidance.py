"""The editing doctrine teaches the premium short-form grammar.

Valmera's quality ceiling was policy: prompts and skills defaulted to
restraint, silence and static type while the owner's reference reels show
committed art direction, motion typography, sound edited to picture and eased
camera moves. These tests pin the measurable grammar and keep the retired
restraint defaults from creeping back.

Sound and music follow the owner's own decisions: only the approved library
of real recordings, placed sparsely on meaningful on-screen moments (never
"whoosh wars"), and music only when the user asks for it or supplies a track.
The rejected synthesized kit and the "every reel carries a music bed" default
must not come back.
"""

import agent_prompt
import agent_tools


def _skill(name):
    text = agent_prompt.read_skill_text(name)
    assert text, name
    return text


def _flat(text):
    """Skill prose is hard-wrapped; compare phrases across line breaks."""
    return " ".join(text.split())


def test_core_prompt_states_the_premium_short_form_standard():
    p = agent_prompt.CORE_PROMPT
    assert "PREMIUM SHORT-FORM IS THE STANDARD FOR REELS" in p
    for phrase in ("0.1-0.6 s", "hook line as text by 1.5 s",
                   "every 0.3-0.6 s", "2-4 hero moments",
                   "13-20 dB under the voice",
                   "list_sound_library",
                   "never on captions or ordinary cuts",
                   "about one sound every 4-5 s at most",
                   "never the same sound twice within ~3 s",
                   "music only when the user asks for it or supplies a track",
                   "never on your own initiative",
                   "alternating tight/wide framing across jump cuts",
                   "strength is magnification minus 1",
                   "never a whoosh on every caption",
                   "deliberate choice for that passage",
                   "Never invent numbers"):
        assert phrase in p, phrase
    # A CTA is conditional and never carries invented identity or offers.
    assert "native CTA only when the user or brief asks for one" in p
    # Rendered looks read a complete preview, not the changed-section proof.
    assert "look_at(rendered=true) reads only a COMPLETE preview" in p
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
                   "2–4 hero moments", "motion_look", "alternating framing",
                   "13–20 dB", "never a small card on a flat black void",
                   "comment_cta", "only when the user or brief asks for one",
                   "Never invent a handle", "magnification − 1",
                   "Never invent facts"):
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
                   "render_preview(complete=true)", "look_at(rendered=true",
                   "`mute_captions` explicitly", "verified=false"):
        assert phrase in text, phrase


def test_audio_teaches_the_sound_library_and_sparse_usage():
    text = _flat(_skill("audio"))
    for phrase in ("list_sound_library()", "storage_key='sound:<id>'",
                   "always pass the suggested gain", "sfx=true", "it HITS",
                   "Never pre-roll by hand",
                   "whoosh_soft_1", "swish_1", "impact_1", "riser_4",
                   "shutter_1", "typing_1", "click_1", "cash_register_1",
                   "heartbeat_1", "Never a whoosh on every caption",
                   "Never a sound on captions, on ordinary cuts",
                   "at most about one sound every 4–5 s",
                   "never the same sound twice within ~3 s",
                   "Zero is a fine answer", "one sound family per short",
                   "Never add music on your own initiative",
                   "the bed sits 13–20 dB under the voice"):
        assert phrase in text, phrase


def test_music_is_added_only_when_the_user_asks():
    music = _flat(_skill("music"))
    for phrase in ("Never add music on your own initiative",
                   "only when the user asks for it or supplies a track",
                   "`find_song(query)`", "not a usage licence",
                   "only when the user explicitly asks for generic background music",
                   "list_music_library tool", "add_library_music tool",
                   "Suggesting is fine"):
        assert phrase in music, phrase
    assert "almost always carries a music bed" not in music


def test_retired_sound_kit_and_default_music_bed_are_gone():
    retired = ("list_sfx_kit", "kit:", "sfx_kit", "kit sound", "sound kit",
               "22 kit", "sub_drop", "whoosh_hard", "riser_short",
               "impact_soft", "music bed present", "almost always carries a "
               "music bed", "no bed on a reel", "add the music bed",
               "never silent between words")
    for name in agent_prompt.skill_names():
        text = _flat(_skill(name)).lower()
        for phrase in retired:
            assert phrase.lower() not in text, (name, phrase)
    core = agent_prompt.CORE_PROMPT
    assert "kit sound" not in core and "a music bed 13-20 dB" not in core
    for name, contract in agent_tools._COMPACT_CONTRACTS.items():
        assert "kit:" not in contract and "list_sfx_kit" not in contract, name


def test_sound_rules_reach_every_department_that_places_sound():
    for name in ("short-form-direction", "transitions", "motion-design",
                 "beats-emphasis"):
        text = _flat(_skill(name))
        assert "4–5 s" in text, name
    sfd = _flat(_skill("short-form-direction"))
    assert "Never on captions or ordinary cuts inside a conversation" in sfd
    assert "MUSIC only when the user asks for it or supplies a track" in sfd
    # Graphics are silent unless the editor opts a moment in.
    assert "graphics are SILENT by default" in _flat(_skill("motion-design"))


def test_stop_down_uses_controls_that_exist():
    # set_music_fit cannot dip mid-track and a cue cannot be shortened: the
    # stop-down splits the bed and restarts it, never just ends it early.
    for name in ("audio", "music", "short-form-direction"):
        text = _skill(name)
        assert "dip the bed with `set_music_fit`" not in text, name
        assert "end a cue early" not in text, name
    audio = _flat(_skill("audio"))
    assert "Never end the bed early without restarting it" in audio
    assert "AUDIO CHECK dead-air line" in audio


def test_rendered_review_needs_a_complete_preview_everywhere():
    for name in agent_prompt.skill_names():
        text = _skill(name)
        if "look_at(rendered=true" not in text:
            continue
        assert "complete" in text, name
    for name in ("review", "motion-design", "zooms", "captions"):
        assert "render_preview(complete=true)" in _skill(name), name


def test_zoom_strength_is_taught_as_magnification_minus_one():
    zooms = _flat(_skill("zooms"))
    assert "STRENGTH IS MAGNIFICATION − 1" in zooms
    for mode in ("`punch`", "`landing`", "`ease`", "`push_in`", "`pulse`",
                 "`shake`"):
        assert mode in zooms, mode
    assert "`push` (" not in zooms
    assert "no more than one camera event per ~1.5 s" in zooms


def test_ctas_are_conditional_and_identity_safe():
    for name in ("short-form-direction", "hooks-retention", "motion-design",
                 "formats"):
        text = _flat(_skill(name))
        assert "asks for" in text, name
        assert "invent" in text, name
    review = _flat(_skill("review"))
    assert "a CTA uses only the handle, keyword and offer" in review


def test_compact_contracts_carry_the_creative_menus():
    contracts = agent_tools._COMPACT_CONTRACTS
    for name in ("list_motion_templates", "add_motion_graphic",
                 "set_motion_graphic", "list_sound_library", "add_sfx",
                 "add_captions", "set_caption_style", "apply_look",
                 "add_zoom", "punch_in_on_emphasis", "set_transitions",
                 "set_picture_card", "look_at"):
        assert name in contracts, name
    assert "'sound:<id>'" in contracts["add_sfx"]
    assert "suggested gain_db" in contracts["add_sfx"]
    assert "about one sound every 4-5 s" in contracts["add_sfx"]
    assert "sound:<id>" in contracts["list_sound_library"]
    assert "Silent by default" in contracts["add_motion_graphic"]
    assert "sound:swish_1" in contracts["set_transitions"]
    assert "only when the user asked for background music" in contracts["apply_look"]
    assert "motion_look" in contracts["add_captions"]
    for look in ("editorial", "creator_punch", "cinematic_doc", "mono_noir",
                 "clean_minimal"):
        assert look in contracts["apply_look"]
    for mode in ("punch", "landing", "push_in", "pulse", "shake"):
        assert mode in contracts["add_zoom"]
    assert "behind_subject" in contracts["add_motion_graphic"]
    assert "mute_captions explicitly" in contracts["add_motion_graphic"]
    # look_at works without a render for source and assembled geometry, and
    # rendered=true reads only a complete preview of the current version.
    assert "no render needed" in contracts["look_at"]
    assert "Requires render_preview" not in contracts["look_at"]
    assert "COMPLETE preview" in contracts["look_at"]
    assert "render_preview(complete=true)" in contracts["look_at"]
    # A look's own transition sounds are read before stacking more.
    assert "read the receipt" in contracts["apply_look"]


def test_compact_catalog_uses_the_contracts():
    compact = {t["function"]["name"]: t["function"]["description"]
               for t in agent_tools.openai_tools(compact=True)}
    for name, contract in agent_tools._COMPACT_CONTRACTS.items():
        if name in compact:
            assert compact[name] == contract
