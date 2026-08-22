"""Creative direction is durable, coherent and semantically closable."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_loop  # noqa: E402
import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import director  # noqa: E402
from schemas import default_edl  # noqa: E402


def _blueprint():
    return director.create_blueprint(
        steps=["build a coherent story cut", "finish picture and sound"],
        brief="premium founder reel",
        treatment="proof-led founder confession",
        decision_basis=[
            "the speaker opens with a contrarian claim",
            "the retention result gives the middle a concrete proof beat",
        ],
        reference_transfer=[
            "transfer the reference's speech-led energy contrast, not its cut count",
        ],
        coherence_rules=[
            "type, picture movement and SFX accent the same rhetorical turns",
            "proof cutaways replace the speaker only when they add evidence",
        ],
        alternatives_rejected=[
            "constant stock montage weakens speaker credibility",
            "maximal kinetic type competes with the concrete proof",
        ],
        audience="ambitious first-time founders",
        platform="Instagram Reels",
        objective="hold attention and earn a profile visit",
        narrative_arc=["counterintuitive hook", "proof", "specific payoff"],
        sequence_map=[
            {"role": "hook", "anchor": "Most founders scale too early",
             "purpose": "create a credible contradiction",
             "visual": "speaker close-up; one selective designed phrase",
             "sound": "dry voice then the pulse enters", "energy": .55,
             "source_start_s": 1.2, "source_end_s": 5.8,
             "evidence_ids": ["sent1"]},
            {"role": "proof", "anchor": "our retention fell by half",
             "purpose": "make the consequence concrete",
             "visual": "reviewed retention chart cutaway",
             "sound": "pulse continues; one restrained impact", "energy": .8},
            {"role": "payoff", "anchor": "talk to customers first",
             "purpose": "land the actionable lesson",
             "visual": "return to steady eye contact and hold",
             "sound": "remove effects and resolve the music", "energy": .4},
        ],
        caption_direction="editorial grotesk, two lines max, key words only",
        motion_direction="restrained moves motivated by speech emphasis",
        broll_direction="specific proof imagery, never generic handshakes",
        music_direction="modern pulse with room for the voice",
        sfx_direction="one cohesive soft tactile family on visible events",
        color_direction="clean neutral skin with one warm accent",
        acceptance_criteria=["hook is clear without prior context",
                             "no caption collision or clipped face"],
    )


def _motion_language():
    return {
        "principle": "settled credibility interrupted only by earned proof",
        "density": .35, "intensity": .42, "contrast": .8,
        "stillness_rule": "hold setup and resolution after the idea lands",
        "motifs": [{
            "id": "earned_push",
            "behavior": "one eased camera/type convergence toward the proof",
            "trigger": "the strongest concrete evidence replaces assertion",
            "domains": ["camera", "type"],
        }],
    }


def test_blueprint_carries_every_craft_dimension_and_closes_from_evidence():
    bp = _blueprint()
    assert director.status(bp)["state"] == "in_progress"
    bp = director.update_progress(
        bp, completed_steps=[1, 2], passed_criteria=[1, 2, 3],
        evidence="EDL v8 and preview v8")
    assert director.status(bp)["state"] == "complete"
    block = director.prompt_block(bp)
    for phrase in ("premium founder reel", "proof-led founder confession",
                   "Decision basis:", "Reference transfer:",
                   "Coherence rules:", "Weaker routes rejected:",
                   "Instagram Reels", "B-roll:",
                   "Music:", "SFX:", "SEQUENCE MAP", "Beat 2 [proof]",
                   "picture=reviewed retention chart", "[completed]"):
        assert phrase in block
    assert "evidence=sent1" in block


def test_new_user_request_inherits_style_but_not_old_completion():
    old = director.update_progress(
        _blueprint(), completed_steps=[1, 2], passed_criteria=[1, 2, 3],
        evidence="old preview")
    new = director.create_blueprint(
        steps=["finish picture and sound"], previous=old,
        source_request="Make the captions smaller")
    assert new["caption_direction"] == old["caption_direction"]
    assert new["music_direction"] == old["music_direction"]
    assert new["treatment"] == old["treatment"]
    assert new["coherence_rules"] == old["coherence_rules"]
    assert new["sequence_map"] == old["sequence_map"]
    assert new["step_states"][0]["status"] == "pending"
    assert new["acceptance_checks"][0]["status"] == "pending"


def test_bare_continue_does_not_replace_the_original_source_request():
    old = director.create_blueprint(
        steps=["build the story"], source_request="Make a premium launch reel")
    resumed = director.create_blueprint(
        steps=["finish the sound"], previous=old, source_request="Continue")
    assert resumed["source_request"] == "Make a premium launch reel"

    redirected = director.create_blueprint(
        steps=["change the captions"], previous=old,
        source_request="Make the captions smaller")
    assert redirected["source_request"] == "Make the captions smaller"


def test_same_turn_replan_can_preserve_matching_progress():
    old = director.update_progress(_blueprint(), completed_steps=[1],
                                   evidence="EDL v3")
    revised = director.create_blueprint(
        steps=["build a coherent story cut", "choose a better soundtrack"],
        previous=old, preserve_progress=True)
    assert revised["step_states"][0]["status"] == "completed"
    assert revised["step_states"][1]["status"] == "pending"


def test_blueprint_derives_evidence_gates_from_its_own_direction():
    bp = director.create_blueprint(
        steps=["build the reel"], format="podcast reel",
        objective="make one argument memorable",
        sequence_map=[
            {"role": "hook", "anchor": "the opening claim",
             "purpose": "create the question", "energy": .6},
            {"role": "payoff", "anchor": "the final answer",
             "purpose": "resolve the question", "energy": .45},
        ],
        caption_direction="clean phrase captions",
        broll_direction="specific proof only",
        music_direction="subtle measured pulse")
    criteria = "\n".join(bp["acceptance_criteria"])
    assert "complete preview" in criteria
    assert "self-contained and coherent" in criteria
    assert "platform-safe" in criteria
    assert "generic wallpaper" in criteria
    assert "measured candidate evidence" in criteria
    assert "rendered sequence realizes each planned beat" in criteria
    assert director.status(bp)["pending_criteria"]


def test_sequence_map_is_bounded_and_rejects_invented_timing_or_energy():
    rows = [{"role": f"beat {i}", "anchor": f"anchor {i}",
             "purpose": "advance the argument", "energy": .5}
            for i in range(20)]
    bp = director.create_blueprint(steps=["build it"], sequence_map=rows)
    assert len(bp["sequence_map"]) == 16

    for bad in (
        [{"role": "hook", "energy": .5}],
        [{"anchor": "claim", "energy": 1.2}],
        [{"anchor": "claim", "source_start_s": 5}],
        [{"anchor": "claim", "source_start_s": 5,
          "source_end_s": 4}],
    ):
        try:
            director.create_blueprint(steps=["build it"], sequence_map=bad)
            raise AssertionError("invalid sequence map must be rejected")
        except ValueError as exc:
            assert "sequence_map" in str(exc)


def test_editorial_family_is_coarse_and_abstains_on_ambiguous_work():
    podcast = director.create_blueprint(
        steps=["select the exchange"], format="podcast reel")
    demo = director.create_blueprint(
        steps=["show the workflow"], format="SaaS product demo")
    assert director.editorial_family(podcast) == "podcast_conversation"
    assert director.editorial_family(demo) == "product_demo_explainer"
    assert director.editorial_family(None, "narrative-vlog") == \
        "narrative_story"
    assert director.editorial_family(None, None, True) == "mixed_other"
    assert director.editorial_family(None, None, False) == "mixed_other"


def test_format_cast_does_not_mistake_platform_or_energy_for_a_format():
    vague = director.editorial_family_cast(
        None, None, True,
        request_text="Make me a fast stunning high energy Instagram reel")
    assert vague["family"] == "mixed_other"
    assert vague["confidence"] < .5
    assert set(vague["candidates"]) == set(agent_tools.editorial_contracts.FAMILIES)
    slate = agent_tools.editorial_contracts.casting_block(vague)
    for family in agent_tools.editorial_contracts.FAMILIES:
        if family != "mixed_other":
            assert f"- {family}:" in slate

    performance = director.editorial_family_cast(
        None, None, True,
        request_text="Cut these dance takes as a music-led live performance")
    assert performance["family"] == "music_led_performance"
    assert performance["confidence"] >= .75


def test_explicit_blueprint_family_beats_keyword_inference_and_persists():
    plan = director.create_blueprint(
        steps=["build it"], editorial_family="commercial_brand",
        format="Instagram product reel")
    cast = director.editorial_family_cast(
        plan, "talking-head-promo", True,
        request_text="make this social")
    assert cast == {
        "family": "commercial_brand", "confidence": 1.0,
        "reason": "the creative blueprint explicitly selected it",
        "candidates": ["commercial_brand"],
    }
    assert "Editorial family: commercial_brand" in director.prompt_block(plan)


def test_plan_close_pushback_is_semantic_and_only_once():
    ctx = SimpleNamespace(edit_plan=_blueprint(),
                          plan_revised_this_turn=True,
                          versions_written=[2])
    messages = []
    assert agent_loop._plan_completion_pushback(ctx, messages, False)
    assert "pending steps=[1, 2]" in messages[-1]["content"]
    assert not agent_loop._plan_completion_pushback(ctx, messages, True)
    ctx.edit_plan = director.update_progress(
        ctx.edit_plan, completed_steps=[1, 2], passed_criteria=[1, 2, 3])
    assert not agent_loop._plan_completion_pushback(ctx, [], False)


class _FakeDb:
    def __init__(self):
        self.rows = [{"version": 1, "json": default_edl(20.0)}]
        self.assets = {}
        self.indexes = {}

    def run(self, fn, *args):
        if fn is dbx.latest_edl:
            return self.rows[-1]
        if fn is dbx.insert_edl:
            row = {"version": self.rows[-1]["version"] + 1,
                   "json": args[1]}
            self.rows.append(row)
            return row["version"]
        if fn is dbx.asset_by_key:
            return self.assets.get(args[1])
        if fn is dbx.get_index_by_sha:
            return self.indexes.get(args[0])


def test_atomic_recipe_can_close_the_semantic_steps_it_implements():
    fake = _FakeDb()
    ctx = agent_tools.ToolContext(
        fake, {"id": 3}, {"id": 9, "chat_session_id": 2},
        {"video": {"duration": 20.0, "width": 1920, "height": 1080,
                   "fps": 30, "has_audio": True}}, "/tmp")
    ctx.edit_plan = director.create_blueprint(
        steps=["make it vertical", "give it a warm finish"])
    result = agent_tools.apply_edit_recipe(
        ctx,
        [{"tool": "set_frame", "args": {"ratio": "9:16"}},
         {"tool": "set_color_grade", "args": {"preset": "warm"}}],
        brief="vertical warm finish", completes_steps=[1, 2])
    assert result.startswith("EDL v1 -> v2")
    assert director.status(ctx.edit_plan)["state"] == "needs_review"


def test_clean_complete_preview_requires_semantic_closure_before_variation():
    ctx, fake = _tool_ctx()
    ctx.edit_plan = director.create_blueprint(
        steps=["make the frame vertical"],
        format="talking-head social reel",
        acceptance_criteria=["the composition is deliberate"])
    # A durable blueprint loaded from an earlier turn remains authoritative;
    # clean evidence must close taste churn without rewriting the plan first.
    ctx.plan_revised_this_turn = False
    assert agent_tools.execute(
        ctx, "set_color_grade", {"preset": "warm"}).startswith("EDL v1 -> v2")
    ctx.last_preview = {"edl_version": 2}
    ctx.last_visual_critic = {"verdict": "pass", "findings": []}
    ctx.last_taste_version = 2
    ctx.last_taste = []
    ctx.last_audio_qc_findings = []
    ctx.last_audio_review = None
    ctx.last_story_review = None
    ctx.verification_records[2] = {
        "edl_version": 2, "status": "passed", "unresolved_findings": []}

    assert not agent_tools.finishing_checkpoint(ctx)
    names = agent_tools.compact_tool_names(ctx)
    assert "complete_edit_plan_steps" not in names
    assert "get_edl" in names
    assert "apply_edit_recipe" not in names
    assert names & agent_tools.WRITE_TOOLS

    allowed = agent_tools.execute(
        ctx, "set_frame", {"ratio": "9:16", "mode": "crop"})
    assert allowed.startswith("EDL v2 -> v3")
    assert fake.rows[-1]["version"] == 3

    # Direct evidence that an acceptance check failed is the semantic repair
    # path. It restores every normal tool without a count/cost override.
    ctx.edit_plan = director.update_progress(
        ctx.edit_plan, completed_steps=[1], failed_criteria=[1],
        evidence="the clean render still uses the wrong aspect")
    assert director.status(ctx.edit_plan)["state"] == "needs_repair"
    assert not agent_tools.finishing_checkpoint(ctx)
    repaired = agent_tools.execute(
        ctx, "set_color_grade", {"preset": "cool"})
    assert repaired.startswith("EDL v3 -> v4")


def test_finish_checkpoint_never_hides_a_proven_quality_defect():
    ctx, _fake = _tool_ctx()
    ctx.edit_plan = director.create_blueprint(
        steps=["tighten the story"], acceptance_criteria=["story resolves"])
    ctx.plan_revised_this_turn = True
    agent_tools.execute(ctx, "set_color_grade", {"preset": "warm"})
    ctx.last_preview = {"edl_version": 2}
    ctx.last_visual_critic = {"verdict": "repair", "findings": [{
        "severity": "major", "category": "composition", "time_s": 3.0,
        "evidence": "the face is clipped", "repair": "restore the face",
        "confidence": .95}]}
    ctx.last_taste_version = 2
    ctx.last_taste = []
    ctx.last_audio_qc_findings = []

    assert not agent_tools.finishing_checkpoint(ctx)


def test_blueprint_tools_are_retired_from_the_agent_catalog():
    schemas = {row["function"]["name"]: row["function"]
               for row in agent_tools.openai_tools()}
    for name in ("set_edit_plan", "complete_edit_plan_steps",
                 "apply_edit_recipe", "generate_video", "generate_image",
                 "research_music", "search_music",
                 "audition_music_candidates", "fetch_music"):
        assert name not in schemas


def test_motion_authoring_requires_a_free_named_language_and_beat_bindings():
    departments = {
        name: {"mode": ("author" if name == "motion" else "preserve"),
               "purpose": ("guide attention through proof" if name == "motion"
                           else "preserve the source treatment")}
        for name in director.TREATMENT_DEPARTMENTS
    }
    beats = [{"role": "setup", "anchor": "the claim",
              "purpose": "establish credibility", "motion_motif": "hold"},
             {"role": "proof", "anchor": "the measured result",
              "purpose": "turn assertion into evidence",
              "motion_motif": "earned_push"}]
    plan = director.create_blueprint(
        steps=["author the motion grammar"], sequence_map=beats,
        department_plan=departments, motion_language=_motion_language())
    assert plan["version"] == 3
    assert plan["motion_language"]["motifs"][0]["id"] == "earned_push"
    block = director.prompt_block(plan)
    assert "MOTION LANGUAGE — one reusable vocabulary" in block
    assert "motion_motif=hold" in block
    assert "motif earned_push [camera,type]" in block

    with pytest.raises(ValueError, match="missing on beat"):
        director.create_blueprint(
            steps=["author it"], sequence_map=[dict(beats[0],
                                                       motion_motif=None)],
            department_plan=departments, motion_language=_motion_language())
    bad = _motion_language()
    bad["motifs"][0]["id"] = "hold"
    with pytest.raises(ValueError, match="executable motif"):
        director.create_blueprint(
            steps=["author it"], sequence_map=beats,
            department_plan=departments, motion_language=bad)


def test_redundant_hold_motif_is_ignored_when_real_motion_motifs_exist():
    language = _motion_language()
    language["motifs"].insert(0, {
        "id": "hold", "behavior": "keep the frame settled",
        "trigger": "supporting beats", "domains": ["camera"]})
    normalized = director._motion_language(language, strict=True)
    assert [row["id"] for row in normalized["motifs"]] == ["earned_push"]


def test_source_evidence_aliases_repair_only_to_real_ids():
    index = {
        "sentences": [{"id": "s1", "t0": 0, "t1": 1}],
        "shots": [{"id": "1", "start": 0, "end": 1},
                  {"id": "2", "start": 1, "end": 2}],
    }
    beats = [{"evidence_ids": ["shot-1", "shot_0", "sentence-1",
                                "invented"]}]
    assert director.canonicalize_source_evidence_ids(beats, index) == 3
    assert beats[0]["evidence_ids"] == ["1", "1", "s1", "invented"]


def test_source_evidence_is_filled_from_exact_timed_window():
    main = {
        "sentences": [{"id": "s1", "t0": 0, "t1": 1.2},
                      {"id": "s2", "t0": 1.2, "t1": 2.4}],
        "shots": [{"id": "sh1", "start": 0, "end": 2.4}],
    }
    auxiliary = {
        "shots": [{"id": "aux1", "start": 4, "end": 6}],
    }
    beats = [
        {"source_start_s": .8, "source_end_s": 2.0,
         "evidence_ids": []},
        {"source_asset_key": "clip/a.mp4", "source_start_s": 4.2,
         "source_end_s": 5.8, "evidence_ids": []},
    ]
    assert director.canonicalize_source_evidence_ids(
        beats, main, asset_indexes={"clip/a.mp4": auxiliary}) == 4
    assert beats[0]["evidence_ids"] == ["s1", "s2", "sh1"]
    assert beats[1]["evidence_ids"] == ["aux1"]
    assert director.source_evidence_violations(
        beats, main, asset_indexes={"clip/a.mp4": auxiliary}) == []


def test_source_evidence_fill_does_not_hide_unknown_or_missing_index():
    index = {"shots": [{"id": "sh1", "start": 0, "end": 2}]}
    beats = [{"source_start_s": 0, "source_end_s": 1,
              "evidence_ids": ["invented"]}]
    director.canonicalize_source_evidence_ids(beats, index)
    assert beats[0]["evidence_ids"] == ["invented"]
    assert "unknown id" in director.source_evidence_violations(
        beats, index)[0]

    absent = [{"source_start_s": 0, "source_end_s": 1,
               "evidence_ids": []}]
    assert director.canonicalize_source_evidence_ids(absent, {}) == 0
    assert "no evidence_ids" in director.source_evidence_violations(
        absent, {})[0]


def test_set_plan_rejects_prose_only_motion_authoring():
    ctx, _fake = _tool_ctx()
    result = agent_tools.set_edit_plan(
        ctx, ["animate the proof"], treatment="evidence-led movement",
        decision_basis=["the final claim is the only proof beat"],
        coherence_rules=["movement appears only when evidence strengthens"],
        motion_direction="premium cinematic movement",
        sequence_map=[{"anchor": "the claim", "purpose": "land proof"}])
    assert result.startswith(
        "REJECTED: a substantial plan that authors motion needs motion_language")
    assert ctx.edit_plan is None


def test_plan_records_full_family_contract_and_rejects_invented_family():
    ctx, _fake = _tool_ctx()
    invalid = agent_tools.set_edit_plan(
        ctx, ["build the reel"], editorial_family="viral_reel")
    assert invalid.startswith("REJECTED: editorial_family must be one of")
    assert ctx.edit_plan is None

    accepted = agent_tools.set_edit_plan(
        ctx, ["build the reel"], editorial_family="music_led_performance",
        treatment="restrained build to performance release")
    assert accepted.startswith("Plan recorded as a creative blueprint")
    assert "FORMAT CONTRACT NOW ACTIVE — music_led_performance" in accepted
    assert ctx.edit_plan["editorial_family"] == "music_led_performance"


def test_plan_tool_rejects_source_ranges_beyond_real_footage():
    ctx, _fake = _tool_ctx()
    result = agent_tools.set_edit_plan(
        ctx, ["shape the sequence"], sequence_map=[{
            "role": "payoff", "anchor": "the final claim",
            "purpose": "resolve the argument", "energy": .7,
            "source_start_s": 18, "source_end_s": 25,
        }])
    assert result.startswith("REJECTED: sequence_map[1] ends")
    assert ctx.edit_plan is None


def test_substantial_plan_requires_one_evidence_backed_treatment():
    ctx, _fake = _tool_ctx()
    ctx.index["sentences"] = [
        {"id": "sent1", "t0": 1.0, "t1": 6.0,
         "text": "Most founders scale too early."},
    ]
    beat = {"role": "hook", "anchor": "scale too early",
            "purpose": "open a credible contradiction", "energy": .6,
            "source_start_s": 1.2, "source_end_s": 5.8,
            "evidence_ids": ["sent1"]}

    missing = agent_tools.set_edit_plan(
        ctx, ["build the hook"], sequence_map=[beat])
    assert missing.startswith(
        "REJECTED: a substantial sequence treatment needs")
    assert "named chosen treatment" in missing
    assert ctx.edit_plan is None

    accepted = agent_tools.set_edit_plan(
        ctx, ["build the hook"],
        treatment="evidence-led founder confession",
        decision_basis=["sent1 is an intelligible contrarian claim"],
        coherence_rules=["picture, type and sound accent the same claim"],
        alternatives_rejected=["generic montage hides the credible speaker"],
        sequence_map=[beat])
    assert accepted.startswith("Plan recorded as a creative blueprint")
    assert ctx.edit_plan["sequence_map"][0]["evidence_ids"] == ["sent1"]


def test_substantial_plan_accounts_for_every_department_without_forcing_fx():
    ctx, _fake = _tool_ctx()
    ctx.index["sentences"] = [
        {"id": "sent1", "t0": 1.0, "t1": 6.0, "text": "One claim."},
    ]
    result = agent_tools.set_edit_plan(
        ctx, ["build one coherent treatment"],
        treatment="quiet evidence-led proof",
        decision_basis=["sent1 carries the whole claim"],
        coherence_rules=["restraint leaves the claim dominant"],
        sequence_map=[{
            "anchor": "one claim", "purpose": "land the proof",
            "source_start_s": 1.0, "source_end_s": 6.0,
            "evidence_ids": ["sent1"],
        }],
        caption_direction="clean readable phrase captions",
        motion_direction="only if earned; preserve stillness",
        music_direction="no music; keep it dry",
        sfx_direction="no SFX",
        color_direction="neutral warm grade")
    assert result.startswith("Plan recorded as a creative blueprint")
    departments = ctx.edit_plan["department_plan"]
    assert set(departments) == set(director.TREATMENT_DEPARTMENTS)
    assert departments["captions"]["mode"] == "author"
    assert departments["motion"]["mode"] == "preserve"
    assert departments["broll"]["mode"] == "preserve"
    assert departments["music"]["mode"] == "omit"
    assert departments["sfx"]["mode"] == "omit"
    assert departments["color"]["mode"] == "author"
    criteria = "\n".join(ctx.edit_plan["acceptance_criteria"])
    assert "promised as author exists" in criteria
    assert "rendered sequence realizes each planned beat" in criteria
    assert "Department decisions:" in director.decision_block(ctx.edit_plan)
    assert "DEPARTMENT EXECUTION CONTRACT" in director.prompt_block(
        ctx.edit_plan)


def test_department_execution_checks_promises_not_feature_density():
    plan = director.create_blueprint(
        steps=["finish the treatment"],
        department_plan={
            "captions": {"mode": "author", "purpose": "readable speech"},
            "motion": {"mode": "preserve", "purpose": "let stillness win"},
            "music": {"mode": "omit", "purpose": "keep the confession dry"},
            "sfx": {"mode": "preserve", "purpose": "no forced accents"},
            "color": {"mode": "author", "purpose": "neutral skin tone"},
        })
    edl = default_edl(20.0)
    gaps = director.department_execution_gaps(plan, edl)
    assert [row["department"] for row in gaps] == ["captions", "color"]

    edl["captions"] = {"mode": "from_transcript"}
    edl["effects"] = {"grade": "warm"}
    assert director.department_execution_gaps(plan, edl) == []
    summary = director.department_execution_summary(plan, edl)
    assert summary == {
        "decisions": 5, "auditable_promises": 3,
        "fulfilled_promises": 3, "gaps": [],
    }

    edl["music"] = [{"id": "music1", "mute": False}]
    gaps = director.department_execution_gaps(plan, edl)
    assert len(gaps) == 1
    assert gaps[0]["department"] == "music"
    assert "deliberately omitted" in gaps[0]["message"]


def test_animated_captions_are_real_type_motion_but_static_subtitles_are_not():
    plan = director.create_blueprint(
        steps=["author timed type motion"],
        department_plan={
            "motion": {"mode": "author",
                       "purpose": "let spoken type carry the movement"},
        })
    edl = default_edl(10.0)
    edl["captions"] = {
        "mode": "from_transcript",
        "style": {"preset": "classic", "animation": "none"},
    }
    assert director.department_execution_gaps(plan, edl)[0]["department"] == \
        "motion"

    edl["captions"]["style"] = {"preset": "karaoke"}
    assert director.department_execution_gaps(plan, edl) == []


def test_blueprint_cannot_close_an_undelivered_department_promise():
    ctx, fake = _tool_ctx()
    ctx.edit_plan = director.create_blueprint(
        steps=["author captions"],
        acceptance_criteria=["the promised treatment exists"],
        department_plan={
            "captions": {"mode": "author", "purpose": "make speech readable"},
        })
    rejected = agent_tools.complete_edit_plan_steps(
        ctx, completed_steps=[1],
        passed_criteria=list(range(
            1, len(ctx.edit_plan["acceptance_checks"]) + 1)),
        evidence="claimed complete")
    assert rejected.startswith("REJECTED: blueprint cannot close")
    assert "captions was promised" in rejected
    assert director.status(ctx.edit_plan)["state"] == "in_progress"

    fake.rows[-1]["json"]["captions"] = {"mode": "from_transcript"}
    closed = agent_tools.complete_edit_plan_steps(
        ctx, completed_steps=[1],
        passed_criteria=list(range(
            1, len(ctx.edit_plan["acceptance_checks"]) + 1)),
        evidence="caption layer exists in current EDL")
    assert closed.startswith("Blueprint COMPLETE")
    assert director.status(ctx.edit_plan)["state"] == "complete"


def test_missing_department_promise_does_not_block_preview():
    ctx, _fake = _tool_ctx()
    ctx.edit_plan = director.create_blueprint(
        steps=["author captions"],
        department_plan={
            "captions": {"mode": "author", "purpose": "make speech readable"},
        })
    result = agent_tools.render_preview(ctx, complete=True)
    assert not str(result).startswith("REJECTED: READINESS PRECHECK")
    assert ctx.editing_metrics.get("readiness_previews_prevented", 0) == 0


def _motion_execution_plan():
    return director.create_blueprint(
        steps=["author the proof movement"],
        sequence_map=[{
            "role": "proof", "anchor": "the measured result",
            "purpose": "make the evidence land",
            "source_start_s": 2.0, "source_end_s": 5.0,
            "motion_motif": "earned_push",
        }],
        motion_language=_motion_language(),
        department_plan={
            "motion": {"mode": "author",
                       "purpose": "converge on the measured result"},
        })


def test_blueprint_cannot_close_when_motion_exists_on_the_wrong_beat():
    ctx, fake = _tool_ctx()
    ctx.edit_plan = _motion_execution_plan()
    fake.rows[-1]["json"]["effects"] = {"zooms": [{
        "id": "wrong-beat", "start": 12.0, "end": 14.0,
        "strength": .15, "mode": "ease",
        "motion_motif": "earned_push",
    }]}
    all_checks = list(range(1, len(ctx.edit_plan["acceptance_checks"]) + 1))

    rejected = agent_tools.complete_edit_plan_steps(
        ctx, completed_steps=[1], passed_criteria=all_checks,
        evidence="a zoom exists somewhere in the EDL")
    assert rejected.startswith("REJECTED: blueprint cannot close")
    assert "motion beat 1" in rejected
    assert "no overlapping authored event" in rejected

    fake.rows[-1]["json"]["effects"]["zooms"][0].update(
        start=2.2, end=4.8)
    closed = agent_tools.complete_edit_plan_steps(
        ctx, completed_steps=[1], passed_criteria=all_checks,
        evidence="the eased camera event overlaps the measured proof beat")
    assert closed.startswith("Blueprint COMPLETE")


def test_wrong_beat_motion_does_not_block_preview():
    ctx, fake = _tool_ctx()
    ctx.edit_plan = _motion_execution_plan()
    fake.rows[-1]["json"]["effects"] = {"zooms": [{
        "id": "wrong-beat", "start": 12.0, "end": 14.0,
        "strength": .15, "mode": "ease",
        "motion_motif": "earned_push",
    }]}

    result = agent_tools.render_preview(ctx, complete=True)
    assert not str(result).startswith("REJECTED: READINESS PRECHECK")
    assert ctx.editing_metrics.get("motion_contract_gaps", 0) == 0


def test_authored_department_plan_exposes_every_promised_department():
    ctx, _fake = _tool_ctx()
    ctx.user_message = "make it great"
    ctx.edit_plan = director.create_blueprint(
        steps=["execute the treatment"],
        editorial_family="product_demo_explainer",
        department_plan={
            "captions": {"mode": "author", "purpose": "step hierarchy"},
            "motion": {"mode": "author", "purpose": "guide attention"},
            "broll": {"mode": "author", "purpose": "show proof"},
            "music": {"mode": "author", "purpose": "support progression"},
            "sfx": {"mode": "author", "purpose": "confirm actions"},
            "color": {"mode": "author", "purpose": "one product world"},
        })
    names = agent_tools.compact_tool_names(ctx)
    assert "add_captions" in names
    for name in ("add_zoom", "add_overlay", "add_music",
                 "search_sfx", "set_color_grade"):
        assert name in names
    assert "research_broll" not in names
    assert "research_music" not in names


def test_timed_sequence_beats_reject_invented_or_unrelated_evidence_ids():
    ctx, _fake = _tool_ctx()
    ctx.index["sentences"] = [
        {"id": "sent1", "t0": 1.0, "t1": 4.0,
         "text": "A real opening claim."},
        {"id": "sent2", "t0": 12.0, "t1": 16.0,
         "text": "A real payoff."},
    ]
    common = dict(
        steps=["shape the sequence"], treatment="claim to payoff",
        decision_basis=["two exact source claims form an arc"],
        coherence_rules=["all departments follow those two claims"])

    invented = agent_tools.set_edit_plan(
        ctx, sequence_map=[{
            "anchor": "opening", "purpose": "hook", "energy": .6,
            "source_start_s": 1.0, "source_end_s": 4.0,
            "evidence_ids": ["sent99"],
        }], **common)
    assert "unknown id(s) for main source: sent99" in invented
    assert 'evidence_ids=["sent1"]' in invented
    assert ctx.edit_plan is None

    unrelated = agent_tools.set_edit_plan(
        ctx, sequence_map=[{
            "anchor": "payoff", "purpose": "resolve", "energy": .4,
            "source_start_s": 12.0, "source_end_s": 16.0,
            "evidence_ids": ["sent1"],
        }], **common)
    assert "falls outside its cited main source evidence" in unrelated
    assert ctx.edit_plan is None


def _add_indexed_clip(fake, key, sha, duration, shots, sentences=None):
    fake.assets[key] = {
        "storage_key": key, "sha256": sha, "kind": "video_clip",
        "duration_s": duration, "meta": {"filename": key.rsplit("/", 1)[-1]},
    }
    fake.indexes[sha] = {"json": {
        "video": {"duration": duration},
        "shots": shots,
        "sentences": sentences or [],
    }}


def test_auxiliary_sequence_evidence_uses_each_assets_own_clock_and_ids():
    ctx, fake = _tool_ctx()
    # The deliberately colliding id proves that scope, not a globally unique
    # string, decides which evidence window validates the beat.
    ctx.index["shots"] = [{"id": "1", "start": 0.0, "end": 20.0}]
    key = "projects/9/uploads/IMG_1370.MOV"
    _add_indexed_clip(
        fake, key, "clip-sha", 7.0,
        [{"id": "1", "start": 2.0, "end": 4.5}])
    result = agent_tools.set_edit_plan(
        ctx, ["shape the uploaded-footage sequence"],
        treatment="motion-led performance progression",
        decision_basis=["the uploaded clip contains the strongest movement"],
        coherence_rules=["picture and sound rise through the same beats"],
        sequence_map=[{
            "role": "lift", "anchor": "strong movement",
            "purpose": "raise momentum", "source_asset_key": key,
            "source_start_s": 2.1, "source_end_s": 4.4,
            "evidence_ids": ["1"],
        }])
    assert result.startswith("Plan recorded as a creative blueprint")
    assert ctx.edit_plan["sequence_map"][0]["source_asset_key"] == key
    assert f"asset {key} CLIP 2.1-4.4s" in director.sequence_block(ctx.edit_plan)


def test_auxiliary_plan_rejects_unknown_pending_and_out_of_range_assets():
    ctx, fake = _tool_ctx()
    common = dict(
        steps=["shape the sequence"], treatment="evidence-led montage",
        decision_basis=["uploaded picture evidence defines the progression"],
        coherence_rules=["all departments follow the same progression"])
    unknown = agent_tools.set_edit_plan(ctx, sequence_map=[{
        "anchor": "clip beat", "purpose": "advance",
        "source_asset_key": "IMG_1370.MOV",
        "source_start_s": 1, "source_end_s": 2, "evidence_ids": ["1"],
    }], **common)
    assert "is not an asset in this project" in unknown
    assert "never use a filename" in unknown

    pending_key = "projects/9/uploads/pending.mov"
    fake.assets[pending_key] = {
        "storage_key": pending_key, "sha256": None, "kind": "video_clip",
        "duration_s": 5.0, "meta": {},
    }
    pending = agent_tools.set_edit_plan(ctx, sequence_map=[{
        "anchor": "pending beat", "purpose": "advance",
        "source_asset_key": pending_key,
        "source_start_s": 1, "source_end_s": 2, "evidence_ids": ["1"],
    }], **common)
    assert "asset's index is not ready" in pending
    assert "do not remap its seconds onto the main video" in pending

    key = "projects/9/uploads/short.mov"
    _add_indexed_clip(
        fake, key, "short-sha", 3.0,
        [{"id": "shot1", "start": 0.0, "end": 3.0}])
    outside = agent_tools.set_edit_plan(ctx, sequence_map=[{
        "anchor": "impossible beat", "purpose": "advance",
        "source_asset_key": key,
        "source_start_s": 2, "source_end_s": 5,
        "evidence_ids": ["shot1"],
    }], **common)
    assert "beyond asset" in outside
    assert "3.00s duration" in outside


def test_story_wide_auxiliary_sequence_validates_same_ids_in_one_plan_call():
    ctx, fake = _tool_ctx()
    rows = []
    for number in range(1, 6):
        key = f"projects/9/uploads/clip-{number}.mov"
        _add_indexed_clip(
            fake, key, f"sha-{number}", 6.0,
            [{"id": "1", "start": 0.5, "end": 5.5}])
        rows.append({
            "role": f"beat {number}", "anchor": f"clip {number}",
            "purpose": "build one coherent progression",
            "source_asset_key": key,
            "source_start_s": 1.0, "source_end_s": 4.0,
            "evidence_ids": ["1"], "energy": number / 6,
        })
    result = agent_tools.set_edit_plan(
        ctx, ["build the five-clip reel"],
        treatment="one rising performance montage",
        decision_basis=["all five uploaded clips contribute distinct action"],
        coherence_rules=["clip order, motion and sound share one energy arc"],
        sequence_map=rows)
    assert result.startswith("Plan recorded as a creative blueprint")
    assert len(ctx.edit_plan["sequence_map"]) == 5


def test_uploaded_media_comparison_batches_every_asset_and_returns_plan_ids(
        monkeypatch, tmp_path):
    from PIL import Image

    ctx, fake = _tool_ctx()
    ctx.workdir = str(tmp_path)
    ctx.sight_out = True
    keys = []
    for number in range(1, 7):
        key = f"projects/9/uploads/take-{number}.mov"
        keys.append(key)
        _add_indexed_clip(
            fake, key, f"compare-sha-{number}", 6.0,
            [{"id": f"shot-{number}", "start": 0.0, "end": 6.0}])
        fake.assets[key]["id"] = number

    calls = []

    def frames(_ctx, asset, times, **_kwargs):
        calls.append((asset["storage_key"], list(times)))
        pairs = []
        for index, _time in enumerate(times):
            path = tmp_path / f"asset-{asset['id']}-{index}.jpg"
            Image.new("RGB", (320, 180),
                      (20 * asset["id"], 30 + index * 20, 80)).save(path)
            pairs.append((index, str(path)))
        return pairs, None

    monkeypatch.setattr(agent_tools.remote, "frames_available", lambda: False)
    monkeypatch.setattr(agent_tools, "_asset_frames", frames)
    result = agent_tools.compare_uploaded_media(
        ctx, keys, question="Cast a coherent high-energy performance reel",
        samples_per_asset=4)

    assert result.startswith("Compared 6 uploaded asset(s) from 24 real frame(s)")
    assert "on 3 page(s)" in result
    assert len(calls) == 6
    assert [key for key, _times in calls] == keys
    assert len(ctx.pending_images) == 3
    for number, key in enumerate(keys, 1):
        assert f"A{number} storage_key={json.dumps(key)}" in result
        assert f'"evidence_ids":["shot-{number}"]' in result
    assert ctx.editing_metrics["uploaded_media_assets_requested"] == 6
    assert ctx.editing_metrics["uploaded_media_assets_compared"] == 6
    assert ctx.editing_metrics["uploaded_media_frames_compared"] == 24

    repeated = agent_tools.compare_uploaded_media(ctx, keys, samples_per_asset=4)
    assert repeated.startswith("UNCHANGED UPLOADED-MEDIA COMPARISON")
    assert len(calls) == 6


def test_uploaded_media_comparison_never_calls_failed_decode_seen(
        monkeypatch, tmp_path):
    from PIL import Image

    ctx, fake = _tool_ctx()
    ctx.workdir = str(tmp_path)
    ctx.sight_out = True
    keys = ["projects/9/uploads/good.mov", "projects/9/uploads/broken.mov"]
    for number, key in enumerate(keys, 1):
        _add_indexed_clip(
            fake, key, f"failure-sha-{number}", 4.0,
            [{"id": f"shot-{number}", "start": 0.0, "end": 4.0}])
        fake.assets[key]["id"] = number

    def frames(_ctx, asset, times, **_kwargs):
        if asset["storage_key"] == keys[1]:
            return [], "decoder rejected the file"
        path = tmp_path / "good.jpg"
        Image.new("RGB", (320, 180), (30, 60, 90)).save(path)
        return [(0, str(path))], None

    monkeypatch.setattr(agent_tools.remote, "frames_available", lambda: False)
    monkeypatch.setattr(agent_tools, "_asset_frames", frames)
    result = agent_tools.compare_uploaded_media(
        ctx, keys, samples_per_asset=1)

    assert "1 supplied candidate(s) could not be seen" in result
    assert "Every supplied candidate has visual evidence" not in result
    assert "INCOMPLETE DECODES" in result
    assert "decoder rejected the file" in result
    assert ctx.editing_metrics["uploaded_media_assets_requested"] == 2
    assert ctx.editing_metrics["uploaded_media_assets_compared"] == 1


def test_narrow_refinement_can_inherit_a_legacy_unbound_sequence():
    ctx, _fake = _tool_ctx()
    ctx.edit_plan = director.create_blueprint(
        steps=["legacy whole edit"], sequence_map=[{
            "anchor": "old hook", "purpose": "open the edit",
            "source_start_s": 1.0, "source_end_s": 3.0,
        }])
    result = agent_tools.set_edit_plan(
        ctx, ["make the existing captions smaller"],
        caption_direction="same family at a smaller readable scale")
    assert result.startswith("Plan recorded as a creative blueprint")
    assert ctx.edit_plan["sequence_map"][0]["anchor"] == "old hook"


def _tool_ctx():
    fake = _FakeDb()
    ctx = agent_tools.ToolContext(
        fake, {"id": 3}, {"id": 9, "chat_session_id": 2},
        {"video": {"duration": 20.0, "width": 1920, "height": 1080,
                   "fps": 30, "has_audio": True}}, "/tmp")
    return ctx, fake


def test_identical_edl_read_reuses_the_fact_until_version_changes():
    ctx, _fake = _tool_ctx()
    first = agent_tools.get_edl(ctx, compact=True)
    second = agent_tools.get_edl(ctx, compact=True)
    assert '"version": 1' in first
    assert second.startswith("UNCHANGED EDL")
    assert agent_tools.execute(
        ctx, "set_color_grade", {"preset": "warm"}).startswith("EDL v1 -> v2")
    assert '"version": 2' in agent_tools.get_edl(ctx, compact=True)


def test_identical_skill_load_is_not_resent_into_the_same_context():
    ctx, _fake = _tool_ctx()
    first = agent_tools.read_skill(ctx, "captions")
    second = agent_tools.read_skill(ctx, "captions")
    assert len(first) > 200
    assert second.startswith("SKILL ALREADY LOADED")


def test_skill_loading_allows_every_relevant_playbook():
    ctx, _fake = _tool_ctx()
    for name in ("captions", "audio", "cutting", "zooms"):
        assert len(agent_tools.read_skill(ctx, name)) > 100
    transitions = agent_tools.read_skill(ctx, "transitions")
    assert len(transitions) > 100
    assert len(ctx._skills_loaded) == 5


def test_exact_additive_write_is_idempotent_across_unrelated_edl_changes():
    ctx, fake = _tool_ctx()
    args = {"text": "ONE IDEA", "start": 1.0, "end": 3.0,
            "template": "title"}
    first = agent_tools.execute(ctx, "add_text", args)
    assert first.startswith("EDL v1 -> v2")
    assert len(fake.rows[-1]["json"]["texts"]) == 1
    duplicate = agent_tools.execute(ctx, "add_text", args)
    assert duplicate.startswith("NO CHANGE")
    assert len(fake.rows[-1]["json"]["texts"]) == 1

    # An unrelated grade changes the EDL signature, but the exact text layer
    # the first call created is still present, so replay remains redundant.
    assert agent_tools.execute(
        ctx, "set_color_grade", {"preset": "warm"}).startswith("EDL v2 -> v3")
    duplicate = agent_tools.execute(ctx, "add_text", args)
    assert duplicate.startswith("NO CHANGE")
    assert len(fake.rows[-1]["json"]["texts"]) == 1


def test_changed_write_arguments_remain_a_real_creative_option():
    ctx, fake = _tool_ctx()
    assert agent_tools.execute(
        ctx, "add_text", {"text": "HOOK", "start": 1, "end": 2,
                          "template": "title"}).startswith("EDL v1 -> v2")
    assert agent_tools.execute(
        ctx, "add_text", {"text": "PAYOFF", "start": 5, "end": 6,
                          "template": "title"}).startswith("EDL v2 -> v3")
    assert len(fake.rows[-1]["json"]["texts"]) == 2
