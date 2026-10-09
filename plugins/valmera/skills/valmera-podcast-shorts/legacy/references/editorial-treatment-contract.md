# Story-Specific Editorial Treatment Contract

Use this contract after story selection and reference analysis, before creating
any child task. The coordinator must reason from each story independently. A
topic, reference, or successful treatment from one sibling is not a preset for
the rest.

## Contents

- Infer the story rather than a genre label
- Choose a treatment with common sense
- Transfer the reference by relationship
- Give each child a complete direction
- Judge the final result against the story
- Return the treatment assignment fragment

## Infer the story rather than a genre label

Read the complete selected transcript arc, source shots, visually observable
speaker performance, reference profile, and audience/platform brief. Describe these axes before
choosing techniques:

- narrative mode: conversation, explanation, retrospective/profile,
  breakthrough, prediction/vision, intimate reflection, debate/tension,
  anecdote, comedy, demonstration, or an honest hybrid;
- audience promise: what the viewer should understand, feel, remember, or do;
- emotional trajectory: for example curiosity to awe, struggle to triumph,
  tension to relief, intimacy to insight, or humor to punchline;
- evidence need: speaker expression, topical proof, archival chronology,
  diagrams, scale/world imagery, product/action footage, or none;
- performance strength: whether the speaker's face, timing, and delivery carry
  the picture or need visual support;
- pace and density: reflective, natural, propulsive, escalating, or variable;
- source-picture fitness: which exact spans are already useful and which must
  be reframed, repaired, or covered.

Use only these taxonomy-v1 narrative-mode tokens in machine-readable fields:
`conversation`, `explanation`, `retrospective_profile`, `breakthrough`,
`prediction_vision`, `intimate_reflection`, `debate_tension`, `anecdote`,
`comedy`, and `demonstration`. Human-readable prose may be more specific, but
it must map to one of these tokens. “Inspiring” belongs in audience promise,
emotional trajectory, and treatment—not as a narrative-mode token. A
`classification_status` of `hybrid` uses two concrete modes; `ambiguous` or
`insufficient_evidence` uses null dominant/secondary fields and carries
hypotheses only in the candidate slate.

A `clear` cast has one concrete `dominant_mode` and a null `secondary_mode`.
A `hybrid` cast has two distinct concrete modes. Never populate a secondary
mode for `clear`; that creates an assignment no conforming editor result can
echo.

Do not hide uncertainty behind `other`. Record `clear`, `hybrid`, `ambiguous`,
or `insufficient_evidence`, plus confidence. For a hybrid, name the dominant
and secondary influences only when the evidence genuinely supports that
ordering. For an ambiguous case, inspect more source evidence and compare the
two strongest treatments before assigning a child; do not invent certainty.
`insufficient_evidence` blocks mutation until the coordinator gathers enough
source evidence or explicitly reports the child blocked.

## Choose a treatment with common sense

Techniques must follow the story rather than a universal density rule. These
examples are directional, not templates:

- An inspiring, aspirational, futuristic, or breakthrough arc may earn
  beautiful topical/cinematic B-roll, an emotional build, wider visual scale,
  and inspiring music that rises toward the payoff.
- A natural conversation, nuanced answer, or intimate confession should often
  remain speaker-led, with stable reframing, restrained cuts, selective proof
  shots, and subtle music or no music when a score would cheapen authenticity.
- A retrospective about a person's struggle and success may use chronological
  archival progression, dates or context only when necessary, increasingly
  confident visual energy, and a tasteful motivating score.
- An explanatory or scientific story should prioritize legible evidence,
  diagrams/topical footage, causal ordering, and curious forward motion over
  empty spectacle.
- A tense debate or warning may use deliberate contrast and controlled
  pressure, not horror clichés or random trailer impacts.
- A comedic anecdote should protect setup timing, reaction shots, and the
  punchline; cinematic solemnity would be a failure.

For every chosen technique, be able to finish this sentence: “Use this because
the story/viewer needs ___ at this exact moment.” Remove techniques whose only
reason is that they are fashionable. A clean, compelling speaker shot is an
edit; random B-roll is not.

Choose explicit treatment values for:

- hook construction and opening picture;
- A-roll/B-roll strategy and semantic motifs;
- pace, shot-duration curve, and energy progression;
- caption rhythm within the required premium single-row system;
- one evidence-backed music policy from `none`, `subtle_bed`,
  `cinematic_build`, or `rhythmic_support`, plus the desired direction, entry,
  and payoff. Mark the direction as a creative brief, not an observed property
  of any candidate track;
- transition grammar, reframing behavior, color/texture, and ending behavior.

## Transfer the reference by relationship

The parent must decide three lists for each child. Every row must cite at least
one stable reference observation ID and one exact story transcript/shot
evidence ID; free-floating taste claims are not enough:

1. `transfer`: underlying relationships that support this story, such as
   captions landing on semantic emphasis or visual scale expanding with the
   argument;
2. `adapt`: useful ideas whose visual density, edit tempo, imagery, or intensity must change
   for this story;
3. `reject`: reference choices that would fight the speaker, story, evidence,
   tone, or publishing constraints.

Every cited observation must appear in
`reference_observation_ids_considered`. A single observation/relationship may
have only one disposition for a child: do not place the same decision in more
than one of `transfer`, `adapt`, and `reject`.

Do not ask a child to “copy the reference” or “make it the same.” Do not copy
the exact footage, music, caption wording, hook, or distinctive shot sequence.
Reliable visible reference track identity/source metadata may inspire a
conservative music search direction. Aggregate reference signal facts cannot be
attributed to its music component. Record rationale, confidence, and
limitations; never invent mood, instrumentation, character, or entry behavior,
and never treat the reference as proof that its exact song should be used.

Different siblings may transfer different parts of the same reference. A
conversational child may borrow only caption restraint and clean reframing,
while an inspiring child borrows its rising visual energy and uses the visible
track identity only as a conservative clue when selecting a separately licensed
candidate. This is
correct generalization, not inconsistency.

A frozen parent reference may also be genuinely inapplicable to one child.
Keep the shared parent reference asset/video/SHA and profile version as proof
that it was considered, cite the observation IDs reviewed, provide an
evidence-backed `inapplicability_rationale`, and record any rejected
relationships. Set the child reference asset/storage fields null and keep
`transfer`/`adapt` empty. Do not relabel an irrelevant reference as `partial`
or fabricate a child copy merely to satisfy lineage.

## Give each child a complete direction

Pass the treatment as dynamic assignment data, separate from the static editor
contract. Include exact source evidence, contract/profile versions, an input
fingerprint, and a factual `decision_basis`; do not send hidden chain-of-thought.
Compute `assignment_input_fingerprint` with
`scripts/canonical_fingerprint.py --kind assignment --input <assignment.json>`.
The helper excludes only its own fingerprint field; all source, story,
reference, treatment, evidence, and exclusion data remain bound. An assignment
contains no runtime lease or attempt locator and remains byte-for-byte immutable
across initial and repair attempts.

In `source`, bind both the exact coordinator-approved parent interval and the
authoritative child seed interval returned by materialization, together with
the source duration and YouTube/video and asset identities. Both intervals
must be ordered and lie inside that frozen source duration. Equal intervals require
`seed_snap_reason=none`; unequal intervals require
`seed_snap_reason=word_boundary_snap`, the authoritative verification route,
and a SHA-256 evidence digest. There is no invented numeric drift tolerance:
preserve the backend's exact bounds. Editors and parent QC echo this complete
object as immutable `source_lineage`.

Set one stable assignment ID and reuse it after resume. Mark the assignment
`ready_for_editor`, `requires_pre_mutation_recast`, or
`blocked_before_mutation`; a blocked assignment requires a concrete reason and
nonempty evidence IDs and must never receive a Valmera mutation lease.
For `requires_pre_mutation_recast`, include exactly two candidate modes with
confidence, evidence IDs, treatment name, editorial thesis, and risk if wrong.
The recast assessment then records supporting and contradicting evidence for
each candidate and the exact decision the editor must resolve. Every inspected
or cited recast evidence ID must come from the corresponding frozen candidate
slate; the approval step cannot introduce new evidence. The editor returns the separate
`PRE_MUTATION_RECAST` schema before any edit lease or Valmera call. The
coordinator persists that artifact itself with `status=approved` or
`status=blocked`; a conversational message is not approval. Approval preserves
the assignment ID/fingerprint, schema/prompt/taxonomy/treatment/reference
versions, and canonical recast-input fingerprint; selects a candidate present
in both the assignment slate and recast assessments; and records the exact
approved clear/hybrid cast and treatment delta. It carries no runtime lease and
is not a new generic assignment. A blocked recast is durable terminal
pre-mutation evidence and does not require fabricated editor or QC results.
The coordinator may add only decision/outcome fields while finalizing the
pending artifact; `recast_input_fingerprint` remains byte-for-byte identical
for either approval or block and continues to identify the editor's exact
pre-approval input.
The child must follow the treatment unless its direct
inspection finds contradictory evidence. In that case it must pause before
mutation and return the contradiction to the parent rather than silently using
a generic style.

Preassign sibling-distinct motifs and exclusions. Do not force uniqueness by
lowering relevance or quality, and allow zero motifs when the performance is the
right picture. Shared caption brand rules may remain consistent while story
imagery, pace, and music vary.

## Judge the final result against the story

During parent QC, exhaust the shot/EDL evidence and inspect rendered frames at
gaps of at most 1.0 second plus every edit boundary/key moment. Exhaust all
transcript/caption pages and ASR warnings, run deterministic audio checks, and ask both:

- Does the edit fulfill this story's audience promise and emotional trajectory?
- Did it transfer/adapt/reject the reference intelligently rather than imitate
  it mechanically or ignore all useful evidence?

Judge music only through an explicitly labeled Codex inference from the story,
reliable track identity/metadata, exposed source-track tempo/beat-sample/energy-
landmark facts, exact EDL
facts, and aggregate whole-mix QC. Fail an edit that is technically busy but emotionally wrong, a conversation
overloaded with spectacle, an inspiring arc left visually flat, a retrospective
with random non-chronological filler, a comic story scored solemnly, or any
other treatment that contradicts its own evidence. Return timestamped repairs
to the same child.

## Return the treatment assignment fragment

Return the complete payload required by
`schemas/child-assignment.schema.json`, not a loose treatment fragment. Include
the canonical taxonomy/classification branch, cited decision evidence,
story-specific visual/caption/color treatment, complete conditional music
entry/exit policy, reference applicability and evidence-linked transfer rows,
routing/recast state, immutable approved/seeded source-range lineage, and stable
sibling exclusions. Compute the canonical assignment fingerprint, validate
against the frozen selection/acquisition/reference artifacts as applicable,
and persist the exact validated bytes before task dispatch. Do not reconstruct
fields from a prose example.
