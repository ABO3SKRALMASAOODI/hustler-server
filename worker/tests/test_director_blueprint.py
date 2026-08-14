"""Creative direction is durable, coherent and semantically closable."""

import os
import sys
from types import SimpleNamespace

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
    assert director.editorial_family(None, None, False) == "graphic_canvas"


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

    def run(self, fn, *args):
        if fn is dbx.latest_edl:
            return self.rows[-1]
        if fn is dbx.insert_edl:
            row = {"version": self.rows[-1]["version"] + 1,
                   "json": args[1]}
            self.rows.append(row)
            return row["version"]


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
    ctx.plan_revised_this_turn = True
    assert agent_tools.execute(
        ctx, "set_color_grade", {"preset": "warm"}).startswith("EDL v1 -> v2")
    ctx.last_preview = {"edl_version": 2}
    ctx.last_visual_critic = {"verdict": "pass", "findings": []}
    ctx.last_taste_version = 2
    ctx.last_taste = []
    ctx.last_audio_qc_findings = []
    ctx.last_audio_review = None
    ctx.last_story_review = None

    assert agent_tools.finishing_checkpoint(ctx)
    names = agent_tools.compact_tool_names(ctx)
    assert "complete_edit_plan_steps" in names
    assert "get_edl" in names
    assert "apply_edit_recipe" not in names
    assert not (names & agent_tools.WRITE_TOOLS)

    blocked = agent_tools.execute(
        ctx, "set_frame", {"ratio": "9:16", "mode": "crop"})
    assert blocked.startswith("NO CHANGE — the current complete preview")
    assert fake.rows[-1]["version"] == 2

    # Direct evidence that an acceptance check failed is the semantic repair
    # path. It restores every normal tool without a count/cost override.
    ctx.edit_plan = director.update_progress(
        ctx.edit_plan, completed_steps=[1], failed_criteria=[1],
        evidence="the clean render still uses the wrong aspect")
    assert director.status(ctx.edit_plan)["state"] == "needs_repair"
    assert not agent_tools.finishing_checkpoint(ctx)
    repaired = agent_tools.execute(
        ctx, "set_frame", {"ratio": "9:16", "mode": "crop"})
    assert repaired.startswith("EDL v2 -> v3")


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


def test_blueprint_tool_schema_exposes_direction_and_closure():
    schemas = {row["function"]["name"]: row["function"]
               for row in agent_tools.openai_tools()}
    plan_props = schemas["set_edit_plan"]["parameters"]["properties"]
    for key in ("treatment", "decision_basis", "reference_transfer",
                "coherence_rules", "alternatives_rejected", "narrative_arc",
                "caption_direction", "motion_direction",
                "broll_direction", "music_direction", "sfx_direction",
                "color_direction", "sequence_map", "acceptance_criteria"):
        assert key in plan_props
    beat_props = plan_props["sequence_map"]["items"]["properties"]
    assert set(("role", "anchor", "purpose", "visual", "sound", "energy",
                "source_start_s", "source_end_s", "evidence_ids")) <= set(
                    beat_props)
    assert "complete_edit_plan_steps" in schemas


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
    assert "unknown source id(s): sent99" in invented
    assert ctx.edit_plan is None

    unrelated = agent_tools.set_edit_plan(
        ctx, sequence_map=[{
            "anchor": "payoff", "purpose": "resolve", "energy": .4,
            "source_start_s": 12.0, "source_end_s": 16.0,
            "evidence_ids": ["sent1"],
        }], **common)
    assert "falls outside its cited source evidence" in unrelated
    assert ctx.edit_plan is None


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
