# Acceptance Contract

An editor may return `ready` only when every required gate below passes. These
are acceptance gates, not permission to falsify a negative result: a
`needs_repair` or `blocked` result records measured bad or unavailable values
and names every failed gate in structured `issues`.

## Contents

- Story
- Treatment and reference
- Visuals
- Captions
- Speech and music evidence
- Render and CTA
- Return strict JSON only

## Story

- Hook, context, development, payoff, and strong final sentence are present.
- The transcript reads as one self-contained story in source chronology.
- No repeated phrases, filler tails, clipped words, dangling references, or
  synthetic jumps between unrelated podcast sections remain.

## Treatment and reference

- When assigned, the editor inspected the `shorts_reference` across its full
  duration at gaps of at most 1.0 second, took closer samples around every
  boundary/cut/transition observed there, exhausted its transcript/captions,
  and verified its immutable child asset ID. Because the public reference
  surface exposes no exact cut inventory or paginated storyboard, acceptance
  never claims all shot pages, transitions, or unknown cuts were exhausted.
  A story-specific `inapplicable` decision instead preserves the considered
  parent reference/profile identity,
  cited observation IDs, reject evidence, and rationale while keeping the child
  copy and transfer/adapt rows absent. A run with no reference uses the explicit
  `not_supplied` branch.
- The rendered edit fulfills the assigned audience promise and emotional
  trajectory for this story; it is not a generic topic-wide preset.
- Every `transfer`, `adapt`, and `reject` decision from the parent assignment is
  honored or an evidence-based contradiction was resolved with the parent
  before mutation.
- The edit borrows relationships such as pacing, emphasis, or energy—not the
  reference's exact footage, soundtrack, caption wording, or shot sequence.
- Treatment, B-roll density, pace, and music policy fit this narrative mode. A
  conversation is not over-edited merely because an inspiring sibling exists,
  and an inspiring/retrospective story is not left visually flat merely because
  the reference is conversational.
- A `ready` result has resolved classification to `clear` or `hybrid`;
  `ambiguous` or `insufficient_evidence` may return only `blocked` unless the
  immutable assignment is accompanied by a persisted approved
  `pre-mutation-recast` artifact. In that case the result must match its exact
  approved candidate, clear/hybrid cast, and treatment delta, while all
  unchanged fields continue to match the assignment.
- The editor result's complete `source_lineage` exactly echoes the assignment's
  source/video/asset identity and source duration, approved bounds,
  authoritative seeded bounds, snap reason, verification route, and evidence
  digest.

## Visuals

- An EDL-derived visual-purpose map covers the full output clock from 0.00 to
  program end, and rendered-frame evidence samples it at gaps of at most 1.0
  second plus every edit boundary and key moment. Do not claim every-frame
  perception.
- Every visible source/B-roll shot has a documented semantic purpose; there are
  zero accidental wall, empty-background, lost-subject, stale-slide, or
  unrelated-raw-footage intervals.
- The assigned visual identity is distinct from siblings and no forbidden
  sibling asset/source window was reused.
- Every B-roll asset and exact source window was inspected at its start, middle,
  end, and relevant motion/transition points before placement, and its canonical
  provenance plus available usage evidence were
  recorded without inventing a rights verdict.
- Topical YouTube footage was considered before Pexels for every
  identifiable person/event/product/place/science/history/technology beat.
- Pexels does not dominate when enough qualifying topical footage exists; every
  topical Pexels use records why inspected YouTube candidates failed.
- Missing automated `license_status` metadata alone did not reject a visual;
  any known incompatible restriction or publishing problem remains recorded.
- B-roll windows are normally 3.5-5.0 seconds. Any effective continuous hold
  above 5.25 seconds has a must-see justification and none exceeds 10.0 seconds.
- Adjacent same-asset overlays were counted as one effective hold.
- Coverage suits the classified story without generic filler.
- Every unrelated base-picture interval marked `mandatory_cover` is covered.
- Reused assets use visibly different, non-overlapping source windows.
- No children, unwanted text, subtitles, logos, watermarks, fake subject
  substitutes, presentation leaks, or one-frame raw-footage flashes appear.
- Every consecutive B-roll junction was checked around the boundary frames.
- The whole preview has complete sampled visual coverage under the 1.0-second
  maximum-gap rule; the record says it is sampled rather than continuous.
- The speaker is correctly reframed throughout every moving interval; there are
  zero crop-tracking drifts, lost faces, or wall-dominant frames.
- The retention pass found no stagnant, redundant, or semantically empty beat.

## Captions

- Captions begin with the first spoken word at 0.00 within one frame.
- One active caption event and one visual row appear at a time.
- Each state has 1-2 words, never more than 2 words or 26 characters.
- Layout is bottom-safe, single-size flow; no wrap, stack, newline, collision,
  duplicate layer, or unwanted multi-level composition appears.
- Technical names are correct.
- Caption audit passes with zero uncovered spoken words, zero true overlaps,
  zero warnings, `max_words_seen <= 2`, `max_lines_seen == 1`, zero density or
  wrap violations, and no more than 0.08 seconds to the first caption.

## Speech and music evidence

- The authoritative current-preview result/metadata includes
  `audio_model_review=false`. This is Valmera-authored provenance, not an agent
  option or quality verdict: agents may neither set nor override it. Missing or
  true provenance blocks `ready`/parent `pass`.
- Preview-ASR media came from
  `watch_video(kind=timeline, render=true, delivery=url, frames=false)` with no
  time window, size limit, inline delivery, or transcode. A `frames`-enabled
  retrieval or media-review attachment cannot satisfy `ready`/parent `pass`;
  visual inspection uses separate timestamped still-frame tools.
- `program_speech_transcript` comes from local ASR of the actual untouched,
  complete current preview: `source=render_asr`, `render_edl_version` equals
  both the live and preview EDL versions, full processed coverage is recorded,
  and `processing_gaps` is empty. Every detected word plus warnings is retained
  and compared with rendered captions. Platform captions and kept-transcript
  pages are also exhausted, but source/kept transcripts are planning context
  only and cannot satisfy this render gate. ASR is fallible text/timing evidence,
  not proof that every utterance was detected or transcribed accurately.
- For music, at least three plausible commercially usable candidates were
  compared from reliable title, artist, provider/asset identity, license/
  attribution, duration, and exposed tempo/BPM confidence, beat-sample, and
  energy-landmark facts unless the user
  supplied an exact licensed track. Unknown identity or missing required rights
  evidence is recorded rather than invented.
- Codex remains the decision-maker and records the chosen track's fit as an
  explicit inference with rationale and confidence. The inference must connect
  story/transcript, assigned music brief, track identity/metadata, and measured
  properties; it must never be presented as audio perception.
- The selected track's inference result is `fit` and records evidence IDs,
  rationale, and confidence across story, tempo/energy landmarks, timing, speech-density,
  and visual-treatment considerations. If evidence is too weak, do not add
  music. A `ready` result then requires the assigned policy to be `none`;
  otherwise return `needs_repair` or `blocked` rather than contradicting the
  immutable assignment.
- Its output interval overlaps the short, its source offset is in range, and
  looping or remaining source duration covers the requested interval.
- When the music policy starts at the hook, the authored EDL starts at output
  0.00. Delayed or payoff cues begin at their assigned output-clock position.
  Do not claim rendered first-music onset from the whole-mix signal.
- Verify the selected track's exact EDL interval, offset, loop, gain, ducking,
  fades, mute state, and source-duration coverage. The aggregate complete-
  preview audio QC must have no unresolved LUFS/true-peak/LRA/silence or other
  audio-audit warning. It cannot isolate music or speech components.
- Dialogue claims are limited to current-render full-stream ASR processing,
  exhausted detected transcript/caption evidence, word timing,
  authored EDL state, and aggregate whole-mix QC. Do not claim human-experienced
  clarity, component masking, or isolated speech/music levels.
- Music remains configured through the exact final program frame with EDL
  `fade_out_s=0` when the story uses music. No whole-program fade-out is authored
  when CTA music carry is required.
- When the assigned policy is `none`, require a story-specific no-music
  justification, zero selected track, no authored music item, null track/timing/
  license/measurement fields as required by the schema, and no CTA music carry;
  never fabricate track evidence merely to satisfy the result shape.

## Render and CTA

- The latest live EDL was rendered and reviewed through exhaustive evidence
  pages plus sampled frames with at most 1.0-second gaps and every boundary/key
  moment.
- Preview EDL version equals final live EDL version.
- The built-in CTA was not duplicated in the EDL.
- The editor acknowledges that the normal preview excludes the CTA and does
  not falsely claim to have verified the final-export-only tail.

## Return strict JSON only

Return only the exact object required by `schemas/editor-result.schema.json`.
Build it from the live evidence, validate it against the immutable child
assignment—and the approved recast when applicable—with repeatable
`scripts/validate_contract.py --against ...` arguments, then persist the
validated bytes. Runtime lease ID/generation/purpose/repair round describe only
the current edit attempt and do not mutate assignment lineage. Do not
reconstruct the result from a prose example, rename fields, or omit conditional
null branches.

For `needs_repair` or `blocked`, retain the same identity and evidence shape but
report failures truthfully. More-than-10-second effective holds, a late or
absent authored music item/EDL interval, null track/license fields when no usable candidate exists, an
incompletely inspected or unplayable reference, uninspected used provenance,
stale EDL versions, and incomplete visual coverage are allowed only as explicit
failure evidence. An ordinary failure still requires complete sampled visual
coverage, current-render full-stream ASR processing plus exhausted
transcript/caption pages,
deterministic audio checks,
and exhausted evidence/audit pagination. Incomplete visual coverage requires a
corrupt/unplayable-render issue; its declared gaps must exactly complement the
inspected intervals and match the timestamped render-failure evidence. It never
passes.
