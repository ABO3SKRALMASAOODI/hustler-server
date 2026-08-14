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


def test_unknown_contract_abstains_to_mixed_other():
    assert editorial_contracts.contract("invented-format") is \
        editorial_contracts.contract("mixed_other")
