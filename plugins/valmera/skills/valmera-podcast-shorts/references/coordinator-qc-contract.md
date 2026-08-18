# Coordinator QC Contract

Prompt version: `valmera-shorts-editor-v6`.

Run this independent acceptance pass only after a child editor finishes and
returns no outstanding jobs. The coordinator—not the child—owns this verdict.
Keep the next child paused until this short passes or is honestly blocked.

Read `run-state-contract.md` completely and grant the coordinator the exact QC
lease for this child/round before inspection. Immediately before every Valmera
read, visual-inspection, audit, or render request, obtain a `begin-call` permit; make one
request; then close that permit with `end-call` and the authoritative current
job IDs. `check-lease` is diagnostic only. Never inspect outside the QC lease,
guess an empty job list, or advance while a permit is in flight.

## Contents

- Establish current evidence
- Independently judge the story
- Independently judge the treatment and reference adaptation
- Independently judge captions
- Independently judge the complete picture
- Independently judge B-roll
- Independently judge music and sound
- Repair loop
- Return strict parent-QC JSON

## Establish current evidence

1. Open the immutable `child_project_id`; never select by title.
2. Preserve the editor's reported EDL versions, audio measurements, and visual
   provenance verbatim in `editor_claims`; these are claims to audit, not the
   parent's observations. Independently read live project state, EDL, kept transcript, assets, caption configuration,
   and audio configuration. Follow every cursor/section/page to exhaustion for
   EDL items, assets, kept transcript, caption events, and audit results; record
   page counts and covered intervals. Never certify a full timeline from the
   first page alone.
3. Require the latest complete preview EDL version to equal the live EDL. A
   missing, failed, partial, or stale preview is `repair_required`.
   Independently require the preview result/metadata's Valmera-authored
   `audio_model_review=false` provenance. The coordinator must not set,
   override, or reconstruct it. Missing or true provenance is a product blocker
   and cannot yield parent `pass`.
4. Under the QC lease, read live EDL version `N`, then independently request
   `watch_video(kind=timeline, render=true, delivery=url, frames=false)` with no
   time window, `max_mb`, inline delivery, or other windowing/transcoding
   control. `frames=false` is mandatory: this call is URL acquisition only and
   must not attach media-review blocks or instructions. Perform visual
   inspection separately with explicit timestamped still-frame tools. Wait for
   its render job when needed, download the returned untouched complete-preview
   URL to a temporary file, require its preview EDL version to be `N`, and
   re-read live EDL as `N`; a version-pinned preview `download_url` is also
   valid. Run `scripts/transcribe_candidate.py <current-preview-media> --output
   <parent-render-asr-evidence.json>` over its full audio stream. Persist the
   helper JSON, local-media SHA-256, EDL version, and evidence digest before
   deleting the temporary media. Windowed, inline-size-limited, transcoded, or
   `frames`-enabled retrieval cannot satisfy parent QC.
5. Populate the parent's own `program_speech_transcript` from that evidence with
   `source=render_asr` and `render_edl_version=N`. Require complete nonempty
   processed coverage and no processing gaps for `pass`; retain all detected
   words and warnings and compare them with rendered captions. Do not copy the
   child's ASR result as the parent's evidence. Source/kept transcripts are
   planning context only and cannot satisfy this current-render gate. ASR is
   fallible and proves neither detection nor transcription accuracy; zero
   detected words does not prove no speech.
6. Exhaust the current preview's shot/EDL pages and inspect raw rendered frames
   from output 0.00 to program end with adjacent samples at most 1.0 second
   apart. Additionally inspect every shot boundary, B-roll junction/replacement,
   opening, payoff, and final tail. Record exact sampled timestamps and maximum
   gap. This is complete sampled visual coverage, not continuous native-video or
   every-frame perception. Separately exhaust the parent render ASR output and
   warnings, kept transcript/captions, and deterministic caption/audio audits
   after the visual pass.

Close every required sampled-visual coverage gap before sending a repair. Combine all defects
from the completed parent pass into one packet so the child does not receive a
piecemeal stream of fixes. The only exception is an unplayable/corrupt render,
which is itself a render repair and prevents full visual inspection. In that failure
branch preserve the truthful sampled timestamps and cite the exact
`corrupt_render` or `unplayable_render` reason code; never fabricate gap-free
coverage. Record every unsampled span beyond the 1.0-second limit, and make the
timestamped render-failure evidence cover those same gaps. Every ordinary
`repair_required` pass still requires the full dense-frame visual pass,
current-render full-stream ASR processing plus exhausted transcript/caption
pages, and exhausted evidence and deterministic-audit pagination.

Treat any automatically generated visual, story, or audio critic output only as
a lead. It is not creative evidence and may not silently decide the verdict.
Independently inspect raw pixels, transcripts, metadata, EDL state, and permitted
measurements. Override an automated finding only with cited direct evidence; if
the backend veto cannot be overridden, report a product blocker. Never copy a
critic's subjective audio conclusion into a v6 artifact.

Echo the assignment's complete immutable `source` object as `source_lineage`,
including source duration, both approved and authoritative seeded child
bounds, snap reason, verification method, and evidence digest. Parent QC may disagree with the
editor's timing, signal measurements, visual inspection, provenance verdicts, effective
holds, or current EDL versions, but it may not rewrite this source lineage.

## Independently judge the story

Require one coherent, chronological micro-story with an understandable hook,
context, development, payoff, and strongest final sentence. Fail disconnected
quotes, abrupt topic jumps, dangling references, repeated ideas, clipped words,
weak material after the payoff, or visuals that imply a different story.

## Independently judge the treatment and reference adaptation

Read the coordinator-authored `story_profile`, `treatment`, and any
`reference_transfer` assignment. When assigned, inspect the inherited reference
across its full duration at gaps of at most 1.0 second and take closer samples
around every boundary/cut/transition observed there. Exhaust its transcript/
captions and verify its identity; do not rely on the child's summary.

The public reference surface exposes no exact shot/cut inventory or paginated
storyboard. Parent QC must record its sample schedule and observed-boundary
checks, but never claim all reference pages, transitions, or unknown cuts were
exhausted or proven.

Judge whether the finished edit:

- echoes the exact treatment/profile versions and assignment input fingerprint
  rather than an older or sibling assignment;

- fulfills this story's audience promise and emotional trajectory;
- uses a pace, speaker/B-roll balance, visual evidence strategy, and music
  policy appropriate to this narrative mode;
- transfers, adapts, and rejects the assigned reference relationships
  intelligently rather than copying it or ignoring it;
- remains distinct from siblings without sacrificing relevance or quality.

Fail technically polished but emotionally wrong work: over-edited natural
conversation, visually flat inspiration, random/non-chronological retrospective
footage, solemn scoring under comedy, or any generic treatment unsupported by
the source. Common-sense story fit is a gate, not an optional taste note.

## Independently judge captions

Inspect captions throughout the rendered video, including the opening and each
scene change. Require:

- the first caption at the first spoken word, within one frame of 0.00;
- one active caption event, one visual layer, and exactly one rendered row;
- one or two words per state, never more than two or 26 visible characters;
- no stacked/multi-level composition, newline, duplicate, wrap, collision, or
  simultaneous mixed-size phrase;
- a clean modern social-video look: restrained ExtraBold sans text, crisp white,
  dark readable outline, selective warm-yellow emphasis, safe placement, and no
  cheap glow, chrome, rainbow, gimmicky animation, excessive shadow, or ornate
  title-card preset;
- correct names and technical terms;
- caption audit `pass`, zero uncovered words/overlaps/warnings/density/wrap
  violations, `max_words_seen <= 2`, and `max_lines_seen == 1`.

Fail the visual style even when the mechanical audit passes if the rendered
captions look cheap, cluttered, multi-level, oversized, or hard to read.

## Independently judge the complete picture

The EDL-derived visual-purpose map must assign either a deliberately composed
speaker or a semantically useful visual to every output moment. Test it against
the required dense-frame evidence and record exact sampled evidence for any empty wall,
wall-dominant crop, lost/off-screen face, tracking drift, unrelated raw footage,
stale presentation frame, blank/black frame, accidental flash, or uncovered
weak base picture. A clean speaker shot is valid; random B-roll is not required.

Inspect the start, middle, and end of every uncovered moving-speaker interval.
Do not accept a crop that begins correctly and drifts into empty space later.

## Independently judge B-roll

Read the final composited timeline and inspect the start, middle, end, motion
turns, and junction frames of every placed source window. Require:

- direct semantic and emotional relevance to the spoken beat;
- production quality compatible with or better than the source/reference:
  adequate resolution, clean compression, intentional composition and motion,
  coherent lighting/color, and no cheap stock, obvious AI artifacts, cheesy
  acting, watermarks, embedded text, or generic filler;
- no repeated or near-identical asset windows, concepts, or sibling exclusions;
- no reuse of a sibling canonical provider/source ID or SHA-256 plus normalized
  source interval merely hidden behind a different project-local asset key;
- target holds of 3.5-5.0 seconds and normal maximum 5.25 seconds;
- any 5.25-10.0-second effective hold has a specific must-see continuous-action
  or emotional-beat justification; nothing exceeds 10.0 seconds;
- adjacent/overlapping windows from the same asset count as one effective hold,
  so splitting records cannot evade the duration check;
- clean junctions with no one-frame underlying-footage leak.

Independently page through every final asset/provenance record. For each
identifiable topical beat that uses Pexels, inspect the child's recorded
YouTube candidate exceptions and verify the candidates actually failed
relevance, visual quality, provenance, or a known usage restriction. Confirm
that topical YouTube was considered first and that Pexels did not dominate when
stronger qualifying footage existed. For every used YouTube asset, record its
canonical URL, title, uploader, raw nullable automated status, exact available
usage evidence/source/time, attribution, and known restrictions. Missing
automated `license_status` is neutral by itself; never convert missing evidence
into either permission or a failure. Fail invented provenance, an undisclosed
known restriction, or a false Pexels exception.

Fail a technically relevant B-roll clip when its visible quality makes the
short look cheaper.

## Independently judge music and sound

Codex remains the sole creative decision-maker. Do not call another model,
`review_audio`, `research_music`, or `audition_music_candidates`. Use
`search_music` only when its active implementation is confirmed to return
deterministic identity/metadata without a model reviewer. Independently verify
track title, artist, provider/asset identity, license/attribution,
duration, exposed tempo/BPM confidence, beat-sample, and energy-landmark facts,
source URL when exposed, and the editor's
cited evidence. Do not require a source URL when the active tool omits it.
Unknown title/artist identity, insufficient rights, missing required exposed
analysis measurements, or invented metadata fails the music gate.

When the brief calls for music, require an actual music item whose output-clock
start/end match the assigned entry/exit policy and whose source offset/loop
covers that interval. For a hook or whole-program bed, require authored output
start 0.00. For a delayed or payoff cue, require the authored start at the
assigned output-clock position rather than a podcast/source timestamp. Do not
infer first rendered music onset from whole-mix audio.

Recompute the fit as an explicit parent inference from the story transcript,
visual treatment, reliable identity/metadata, and exposed tempo/beat-sample/
energy-landmark facts. Do not claim a full beat grid, full energy curve, or
arbitrary useful source windows.
Record rationale, confidence, and limitations. Do not invent genre, mood,
instrumentation, dialogue quality, or emotional character, and do not describe
the result as direct sensory evidence. Verify EDL intersection, source offset,
loop/source-duration coverage, gain, ducking, fades, mute state, and authored
timing. Exhaust aggregate complete-preview QC for whole-mix LUFS, true peak,
LRA, and silence warnings. It cannot isolate music, speech, component onset,
masking, per-window levels, or ducking quality. Exhausted ASR from the actual
current rendered preview is fallible words/timing evidence only; zero detected
words does not prove no speech. Kept/source transcript evidence cannot replace
that render-bound parent evidence.

If the brief intentionally calls for no music, record that story-specific
justification rather than fabricating a failure. Require no selected track, no
authored music item, null music evidence fields where the schema requires them,
and no CTA carry. Fail music that has unknown identity/rights, lacks required
measurements, is missing/muted/mistimed in the EDL, lacks source/loop coverage,
causes an aggregate QC failure, or lacks enough evidence for a confident fit
inference.

Record those independent observations even when they contradict the editor.
For example, an editor may claim a hook bed starts at 0.0 while the parent finds
the authored EDL start at 2.1 seconds, or may claim a four-second B-roll hold
while the rendered composite remains on the
same asset for eleven seconds. Those contradictions are valid
`repair_required` evidence, not lineage errors.
Likewise, retain editor-reported expected EDL versions in `editor_claims` while
recording different live/preview versions and an explicit render violation.
Only `pass` requires current EDL equality and every parent-observed acceptance
measurement to pass.

## Repair loop

On any failure, return `repair_required` and send the same visible editor one
packet containing only actionable evidence:

```json
{
  "type": "PARENT_QC_REPAIR",
  "prompt_version": "valmera-shorts-editor-v6",
  "child_project_id": 456,
  "reviewed_edl_version": 12,
  "repair_lease_id": "run-123-child-456-parent-repair-1",
  "violations": [
    {
      "gate": "captions",
      "reason_code": "other",
      "start_s": 8.4,
      "end_s": 12.1,
      "evidence": "Two caption rows with six visible words",
      "required_change": "Regenerate single-line captions with at most two words"
    }
  ]
}
```

The child must repair, render the new complete EDL, rerun full-stream ASR on
that new actual preview, apply dense-frame visual inspection, exhaust
transcript/caption pages and ASR warnings, rerun
deterministic audits, and
return its full acceptance JSON with no outstanding jobs. Then repeat this
entire parent pass, including an independent parent render-ASR run, against the
new version. Do not reuse a prior pass result.

After the configured repair cap, require a safe checkpoint with no active,
outstanding, or ambiguous Valmera job, freeze the same task, and return
`blocked` with every unresolved timestamped violation. The coordinator may then
continue to the next queued child. This does not convert the blocked child to
ready; its actual asset/music use still enters sibling exclusions.

## Return strict parent-QC JSON

Return only the exact object required by `schemas/parent-qc.schema.json` and
validate it against the immutable assignment, approved recast when applicable,
and accepted editor result using repeatable `--against` arguments. Build it from
the coordinator's independent live evidence, including the preserved
`editor_claims`, source lineage, required QC-evidence pagination, reference
applicability, structured parent-observed provenance/B-roll verdict, music
branch, and exact observed EDL versions. Stable identities/windows remain
bound; child and parent judgments or measurements need not equal in a repair
artifact. Do not reconstruct it from a prose example. Return `pass` only when
every applicable parent-observed field passes for the current live version.
