import editorial_contracts
import director


EXPECTED_FAMILIES = {
    "podcast_conversation", "talking_head_social",
    "product_demo_explainer", "action_sports_gameplay",
    "music_led_performance", "commercial_brand", "narrative_story",
    "voiceover_montage", "graphic_canvas", "mixed_other",
}


def test_every_editorial_family_has_a_complete_judgment_contract():
    assert set(editorial_contracts.FAMILIES) == EXPECTED_FAMILIES
    for family in EXPECTED_FAMILIES:
        spec = editorial_contracts.contract(family)
        assert spec["driver"]
        assert len(spec["publish_ready"]) >= 3
        assert len(spec["visual_review"]) >= 3
        assert len(spec["reject_if"]) >= 3
        assert len(spec["evidence"]) >= 4
        # Contracts define invariants, not a generic feature-count recipe.
        joined = " ".join(sum(
            [spec["publish_ready"], spec["visual_review"],
             spec["reject_if"], spec["evidence"]], []))
        assert "add exactly" not in joined.lower()
        assert "every 3 seconds" not in joined.lower()


def test_format_contracts_are_materially_different_not_relabelled_generic_text():
    podcast = editorial_contracts.contract("podcast_conversation")
    demo = editorial_contracts.contract("product_demo_explainer")
    action = editorial_contracts.contract("action_sports_gameplay")

    assert "speaker" in " ".join(podcast["publish_ready"]).lower()
    assert "control" in " ".join(demo["publish_ready"]).lower()
    assert "action" in " ".join(action["publish_ready"]).lower()
    assert podcast["driver"] != demo["driver"] != action["driver"]


def test_authoring_contract_explicitly_rejects_feature_checklist_editing():
    block = editorial_contracts.prompt_block("mixed_other")
    assert "publish-ready judgment contract" in block
    assert "not a mandated recipe or density" in block
    assert "feature checklist" in block
    assert "Required evidence" in block


def test_critic_contract_is_visual_and_abstains_from_unseen_story_or_sound():
    block = editorial_contracts.critic_block("podcast_conversation")
    assert "speaker-aware framing" in block
    assert "not_judged" in block
    assert "Do not infer transcript coherence" in block
    assert "mix quality" in block


def test_current_request_can_select_family_before_blueprint_exists():
    assert director.editorial_family(
        None, None, True,
        request_text="Cut this long podcast into one coherent reel") == \
        "podcast_conversation"
    assert director.editorial_family(
        None, None, True,
        request_text="Make a cinematic gameplay montage") == \
        "action_sports_gameplay"


def test_full_family_cast_uses_specific_format_evidence_not_platform_words():
    cases = {
        "Edit this two-person podcast conversation": "podcast_conversation",
        "Make this a speaker-led founder reel": "talking_head_social",
        "Build a SaaS product demo walkthrough": "product_demo_explainer",
        "Cut a cinematic gameplay montage": "action_sports_gameplay",
        "Shape these dance takes as a live performance": "music_led_performance",
        "Create a controlled luxury brand film": "commercial_brand",
        "Tell this wedding documentary story": "narrative_story",
        "Build a narration-led voiceover montage": "voiceover_montage",
        "Make an animated cards motion graphic": "graphic_canvas",
    }
    for request, expected in cases.items():
        cast = director.editorial_family_cast(
            None, None, True, request_text=request)
        assert cast["family"] == expected, request
        assert cast["confidence"] >= .75

    vague = director.editorial_family_cast(
        None, None, True,
        request_text="Make it a beautiful fast professional Instagram reel")
    assert vague["family"] == "mixed_other"
    assert vague["confidence"] < .5


def test_uncertain_cast_exposes_every_concrete_driver_without_a_recipe():
    block = editorial_contracts.casting_block({
        "family": "mixed_other", "confidence": .2,
        "reason": "mixed measured evidence",
    })
    assert "FORMAT CAST — UNCERTAIN" in block
    for family in EXPECTED_FAMILIES - {"mixed_other"}:
        assert f"- {family}:" in block
    assert "not a visual preset" in block


def test_unknown_contract_abstains_to_mixed_other():
    assert editorial_contracts.contract("invented-format") is \
        editorial_contracts.contract("mixed_other")
