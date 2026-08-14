"""Format-aware definitions of publish-ready editorial judgment.

Tool access is not taste.  A good editor chooses a dominant storytelling
driver, protects what the audience must understand or feel, and rejects moves
that are locally flashy but globally incoherent.  These contracts make that
judgment explicit for the director, independent critic, benchmarks and
production cohorts without prescribing a fixed number of cuts, B-roll shots,
zooms, captions, or effects.

They are deliberately invariant-level contracts.  A reference grammar can
suggest one visual skin; the user's brief can invent another.  The contract
only states what must remain true for that kind of edit to work.
"""

CONTRACT_VERSION = 2


_CONTRACTS = {
    "podcast_conversation": {
        "driver": "a self-contained argument or exchange that preserves speaker intent",
        "publish_ready": [
            "the opening is intelligible and creates tension; any setup needed to understand the answer survives",
            "speaker turns, pronouns, reactions and the final insight remain coherent rather than forming a synthetic claim",
            "speech stays dominant while captions, reframing, cutaways, music and SFX clarify instead of competing",
        ],
        "visual_review": [
            "speaker-aware framing and useful reactions, with no crop bias toward empty space",
            "one readable caption system and occasional evidence-driven cutaways rather than restless decoration",
            "visual emphasis follows argumentative peaks and leaves room for natural human timing",
        ],
        "reject_if": [
            "an isolated quote depends on missing context or unrelated answers are spliced together",
            "generic stock replaces a revealing face or has no named evidentiary purpose",
            "constant punch-ins, transitions or SFX turn conversation into a feature demo",
        ],
        "evidence": ["full-recording transcript arc", "speaker turns", "rendered framing/captions", "actual speech mix"],
    },
    "talking_head_social": {
        "driver": "one clear idea carried by a credible speaker from hook through payoff",
        "publish_ready": [
            "the hook makes an intelligible promise and each retained beat advances it toward a payoff or CTA",
            "type, graphics and cutaways create hierarchy around the speaker instead of repeating the transcript literally",
            "motion, stillness, music and SFX share one energy arc with stronger emphasis reserved for stronger ideas",
        ],
        "visual_review": [
            "the face, gesture and intended eye-line remain composed across every reframed shot",
            "captions and designed text have distinct roles, safe placement and a coherent visual family",
            "cutaways are specific proof or illustration and the opening frame has immediate hierarchy",
        ],
        "reject_if": [
            "every phrase receives the same punch, word animation or stock cutaway",
            "captions collide with faces or source text, or decoration obscures speaker credibility",
            "the edit looks like a checklist of available tools rather than one authored piece",
        ],
        "evidence": ["complete speech arc", "visual subject track", "rendered hierarchy", "actual voice/music mix"],
    },
    "product_demo_explainer": {
        "driver": "causal comprehension of the problem, action, product behavior and result",
        "publish_ready": [
            "steps appear in truthful order and the audience can see the control, action and resulting state",
            "reframing and camera paths keep the relevant UI or physical product legible without losing orientation",
            "narration, cursor, callouts, B-roll and sound all point to the same current step",
        ],
        "visual_review": [
            "UI labels, controls and outcomes remain readable at delivery size",
            "zooms travel between inspected targets and settle long enough to understand the action",
            "graphics distinguish instruction, state and proof without hiding the product",
        ],
        "reject_if": [
            "a step, prerequisite or result is skipped, reordered, or shown after the narration has moved on",
            "a crop or overlay hides the control being explained",
            "generic lifestyle stock displaces actual product evidence",
        ],
        "evidence": ["complete task flow", "exact UI frames", "rendered target legibility", "narration alignment"],
    },
    "action_sports_gameplay": {
        "driver": "readable action shaped into escalating visual and musical energy",
        "publish_ready": [
            "the strongest action is established early, then sequences vary anticipation, impact, recovery and escalation",
            "cuts respect readable movement and meaningful musical phrases rather than mechanically firing on every transient",
            "music, impacts, ambience, commentary and captions preserve the action's hierarchy",
        ],
        "visual_review": [
            "the active subject, ball, vehicle, avatar or objective stays visible through reframes and effects",
            "shot scale and cadence vary enough for impacts to feel stronger than connective moments",
            "captions, overlays and effects do not obstruct decisive action or game UI",
        ],
        "reject_if": [
            "cuts interrupt the action before its consequence is legible",
            "every beat receives the same cut/effect and the sequence has no contrast",
            "the soundtrack's tone or intensity contradicts the visible action",
        ],
        "evidence": ["complete action windows", "motion profile", "rendered subject visibility", "actual music/impact mix"],
    },
    "music_led_performance": {
        "driver": "musical structure and performance presence expressed through a coherent visual language",
        "publish_ready": [
            "visual sections recognize phrases, builds, drops and rests instead of treating all beats as equal",
            "performance, lip movement, choreography and instrumental gestures remain synchronized and emotionally credible",
            "shot choice, movement, color and effects evolve with the track while preserving a recognizable motif",
        ],
        "visual_review": [
            "performance faces, bodies and instruments stay intentionally framed through camera movement",
            "effects reinforce musical sections without destroying texture or continuity",
            "visual intensity has contrast, recurring motifs and a deliberate final image",
        ],
        "reject_if": [
            "random beat cuts replace musical phrasing or every transient triggers an effect",
            "sync, performance continuity or emotional tone visibly conflicts with the track",
            "captions or graphics overpower the performer without a concept that earns it",
        ],
        "evidence": ["music structure", "performance sync moments", "rendered motion/effects", "actual final mix"],
    },
    "commercial_brand": {
        "driver": "desire and trust built from a clear brand promise, product proof and controlled craft",
        "publish_ready": [
            "the product or experience has a deliberate hero treatment and the promised benefit is visibly supported",
            "composition, typography, color, motion and sound form one recognizable brand world",
            "pacing creates anticipation and payoff while every detail feels chosen rather than merely polished",
        ],
        "visual_review": [
            "product geometry, logos, packaging, skin tones and defining details remain clean and legible",
            "shot scale, negative space and type hierarchy guide attention toward the promise and proof",
            "transitions and effects have a repeatable design logic rather than a mixed preset stack",
        ],
        "reject_if": [
            "generic stock could advertise any brand or contradicts the actual product",
            "unsupported claims, illegible brand marks or careless color damage trust",
            "feature count substitutes for a concept and the product never receives a true hero moment",
        ],
        "evidence": ["brand/product brief", "actual product pixels", "rendered visual system", "actual music/SFX mix"],
    },
    "narrative_story": {
        "driver": "emotional cause and effect carried through a comprehensible beginning, turn and resolution",
        "publish_ready": [
            "the audience can follow who wants what, what changes, and why the ending matters",
            "shots, narration, reactions, B-roll and music preserve chronology or make any deliberate reordering clear",
            "pacing makes room for setup, escalation, surprise and emotional landing instead of equalizing every moment",
        ],
        "visual_review": [
            "continuity of subject, place, screen direction and visual motifs supports the intended arc",
            "cutaways reveal story information or emotion rather than serving as generic atmosphere",
            "color, type and movement change only when the story earns a new register",
        ],
        "reject_if": [
            "missing references or chronology make actions and emotions unintelligible",
            "music, B-roll or effects tell a different emotion from the footage",
            "the ending stops rather than resolves, reframes or deliberately leaves tension",
        ],
        "evidence": ["full transcript/story map", "scene continuity", "rendered emotional arc", "actual music/dialogue mix"],
    },
    "voiceover_montage": {
        "driver": "specific phrase-to-image correspondence inside one escalating visual world",
        "publish_ready": [
            "each important narration turn receives footage that illustrates, proves or productively contrasts its meaning",
            "shot sequence varies scale, motion and density while remaining visually and tonally coherent",
            "captions, music and SFX reinforce narration rhythm without making every phrase equally loud",
        ],
        "visual_review": [
            "cutaways visibly match their recorded narrative purpose and are strong renditions, not first-result wallpaper",
            "shot-to-shot composition and motion create continuity despite changing sources",
            "text is selective, readable and integrated into the montage rather than duplicating all narration",
        ],
        "reject_if": [
            "generic or contradictory stock is accepted without inspecting its actual downloaded frames",
            "rapid rotation prevents images from reading or repetition makes the sequence feel templated",
            "the visual sequence has no semantic escalation toward the narration's payoff",
        ],
        "evidence": ["narration phrase map", "candidate and downloaded B-roll frames", "rendered sequence", "actual voice/music mix"],
    },
    "graphic_canvas": {
        "driver": "information hierarchy and visual composition revealed in a legible sequence",
        "publish_ready": [
            "every card or scene has one dominant message and enough reading time for its complexity",
            "type, grid, palette, imagery and animation behave as one design system with purposeful variation",
            "transitions reveal relationships, progression or emphasis rather than merely moving objects",
        ],
        "visual_review": [
            "hierarchy, spacing, alignment, contrast and platform-safe margins hold across every layout",
            "animation states enter and settle cleanly without clipped text, empty cards or accidental overlap",
            "repetition creates a system while layout and scale still respond to each message",
        ],
        "reject_if": [
            "important text is unreadable, off-frame, overcrowded or shown too briefly",
            "identical layouts flatten the information or unrelated animation styles break coherence",
            "a blank/placeholder scene or decorative motion survives into the finished program",
        ],
        "evidence": ["complete information outline", "every rendered card state", "animation transitions", "actual soundtrack mix"],
    },
    "mixed_other": {
        "driver": "the user's explicit outcome and the strongest measured material, with one dominant logic chosen before decoration",
        "publish_ready": [
            "the edit can state its audience promise, progression and ending in plain language",
            "story, visual, motion, typography and sound decisions reinforce the same dominant experience",
            "important choices are supported by inspected source, rendered and audio evidence rather than assumptions",
        ],
        "visual_review": [
            "the opening has hierarchy, subjects remain composed, and the sequence has intentional contrast",
            "type, cutaways, effects and transitions share a coherent logic",
            "nothing visible looks like a placeholder, blind crop, first search result or unreviewed preset",
        ],
        "reject_if": [
            "incompatible genre grammars are mixed without a deliberate concept",
            "a feature checklist replaces audience judgment and narrative priority",
            "the editor claims quality for story or sound it did not inspect",
        ],
        "evidence": ["user objective", "representative source coverage", "complete rendered screening", "actual mix where sound is designed"],
    },
}


FAMILIES = tuple(_CONTRACTS)


def contract(family):
    """Return the named contract, abstaining safely to ``mixed_other``."""
    return _CONTRACTS.get(family) or _CONTRACTS["mixed_other"]


def prompt_block(family):
    """Compact authoring contract for the editing model's project state."""
    family = family if family in _CONTRACTS else "mixed_other"
    spec = contract(family)
    return "\n".join([
        f"EDITORIAL BENCHMARK v{CONTRACT_VERSION} — {family}. This is a "
        "publish-ready judgment contract, not a mandated recipe or density.",
        "Dominant driver: " + spec["driver"],
        "Ready when: " + " | ".join(spec["publish_ready"]),
        "Reject when: " + " | ".join(spec["reject_if"]),
        "Required evidence: " + " | ".join(spec["evidence"]),
        "The user's explicit direction wins, but do not waive coherence, "
        "truthfulness, legibility, semantic relevance or evidence.",
    ])


def casting_block(cast):
    """Format decision context without pretending an uncertain brief is known."""
    cast = cast or {}
    family = cast.get("family")
    confidence = float(cast.get("confidence") or 0.0)
    reason = str(cast.get("reason") or "insufficient evidence")
    if family in _CONTRACTS and family != "mixed_other" and confidence >= .75:
        return (f"FORMAT CAST — provisional {family} ({reason}). Confirm it "
                "against the actual pixels and user words; record "
                "editorial_family in set_edit_plan, or choose a different "
                "family when stronger evidence wins.\n" + prompt_block(family))

    lines = [
        "FORMAT CAST — UNCERTAIN. A platform word (reel/TikTok/Instagram), "
        "duration or energy adjective is not a storytelling format. Inspect "
        "speech, actual pixels, motion, uploaded media and references; compare "
        "materially credible drivers below, then record ONE editorial_family "
        "in set_edit_plan. Use mixed_other only for a genuinely hybrid/novel "
        "dominant logic—not as a substitute for looking.",
        f"Current abstention basis: {reason}.",
        "FAMILY DRIVER SLATE:",
    ]
    for name, spec in _CONTRACTS.items():
        if name != "mixed_other":
            lines.append(f"- {name}: {spec['driver']}")
    lines.append(
        "The family chooses an invariant quality contract, not a visual preset, "
        "cut density or permission boundary. A novel treatment remains valid.")
    return "\n".join(lines)


def selection_note(family):
    """Compact same-turn handoff after set_edit_plan chooses a family."""
    family = family if family in _CONTRACTS else "mixed_other"
    spec = contract(family)
    return (
        f"FORMAT CONTRACT NOW ACTIVE — {family}. Dominant driver: "
        f"{spec['driver']}. Guardrails: " + " | ".join(spec["reject_if"])
    )


def critic_block(family):
    """Visual slice of the contract; non-visible dimensions must abstain."""
    family = family if family in _CONTRACTS else "mixed_other"
    spec = contract(family)
    return "\n".join([
        f"FORMAT-SPECIFIC VISUAL BENCHMARK: {family}",
        "Dominant driver: " + spec["driver"],
        "Judge from visible evidence: " + " | ".join(spec["visual_review"]),
        "Relevant rejection patterns: " + " | ".join(spec["reject_if"]),
        "Do not infer transcript coherence, musical fit or mix quality from "
        "still frames; mark those dimensions not_judged unless visible "
        "evidence and supplied context actually prove them.",
    ])
