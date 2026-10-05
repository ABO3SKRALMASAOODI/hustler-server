---
name: valmera-podcast-shorts
description: Turn podcasts and interviews into distinctive, story-led social shorts with Valmera, using Sol and three reusable editors. Use for source acquisition, complete-story selection, reference-informed motion design, efficient independent review, and verified local exports. Does not publish or operate the downstream CRM.
---

# Valmera Podcast Shorts

Make a short someone wants to finish, remember and share. A valid MP4 is only
one part of that job. This is workflow **v8**; the durable `run.json` format
remains v7 so interrupted runs can still be reconciled.

## Read only the instructions for your role

- **Coordinator:** this file, [creative direction](references/creative-v8.md),
  then [production](references/production-v8.md). Read the review section when
  a candidate arrives; do not ingest every historical contract.
- **Editor:** [creative direction](references/creative-v8.md),
  [editing](references/editing-v8.md), and your one assignment.
- **Resuming a pre-v8 run:** preserve that run's frozen brief and the v7
  contracts it used. Never rewrite its accepted artifacts to claim v8 quality.

The unversioned/v6 files and v7 prose are history, not current creative defaults.
Use existing `scripts/run_state.py` for durable state, `compose_short.py` for
initial source-clock plans and native caption recipes, `design_graphics.py` for
editable graphic primitives when useful, and `inspect_cut.py` for bounded media
review evidence. `--help` describes each interface. These scripts save mechanics;
none can certify an engaging story or attractive design.

## Current owner brief

The user wants exceptional autonomous shorts and efficient production on
**gpt-6.1-sol**. Keep the coordinator and editing pool on Sol. Do not switch to a
more expensive model to make the workflow work, or rely on another chat sending
per-clip instructions. Use exactly one coordinator and at most three reused
editor subagents, each with one child at a time. A new podcast is a fresh run.

The current four formats are:

1. `fast-conversation`: complete continuing speech with purposeful, frequent
   visual changes: specific B-roll and authored visual explanations.
2. `hook-to-silent-montage`: a complete spoken premise, then a directly relevant
   silent visual payoff. Post-hook montage <=15s; editorial program <=25s.
3. `silent-action-to-conversation`: 3–4s of recognizable silent action footage
   of the actual subject, then a conversation that fulfills that opening.
4. `headline-conversation`: compelling original conversation, natural camera
   changes, one persistent bold topic headline above the picture, and separate
   expressive dialogue captions. No B-roll or interrupting cards in this lane.

There is **no default lane and no quota**. Choose the structure the story earns.
The black portrait canvas and wider straight-edged picture remain the owner's
preferred composition. Do not replace it with full-height portrait crops by
habit. Preserve both native Valmera brand elements, including the **5-second
ending**, and reserve that time within the 15–45s final. No added music unless
explicitly requested. Preserve requested active-word highlighting. Motion,
scale hierarchy and emphasis are allowed and wanted; flat typography is a
choice for an appropriate passage, not a global safety rule.

The user explicitly permits low-quality archival footage when accepted for a
run. That permission applies to the footage only: new graphics, captions and
branding still render at native HD delivery resolution. Do not upscale and
re-upload every child just to obtain sharp text; Valmera already does that.

## The production loop

1. Create `.tmp/valmera-podcast-shorts/<run-id>` with source, taste,
   assignments, candidates and exports. Never overwrite a previous run.
2. Reuse the existing analyzed references by content hash. The saved four-style
   brief and seven reference files are discoverable under
   `.valmera/podcast-shorts/` and prior run `taste/` directories. Inspect the
   relevant actual frames/motion; reuse the existing analysis instead of
   transcribing/OCR-ing the same references again. Current user corrections
   outrank older profile defaults. Freeze a compact run-local taste profile.
3. Acquire and index the source once. Read the full transcript and inspect
   representative camera changes. Check actual speech/source-clock alignment
   at early, middle and late passages once. Reuse this source across children.
4. Select independently strong complete stories. A fact being interesting to
   us is not enough: identify the viewer's curiosity and the payoff. Compare
   overlapping candidates and retain the stronger complete version. Select
   all that clear the bar; never manufacture a clip count.
5. Materialize the selected source ranges with one explicit `make_shorts`
   call. Children share the indexed parent. Freeze each assignment's IDs and
   story-specific art direction. Never replace originals for routine cuts,
   crops, subtitles, headlines or cards.
6. Launch three editors immediately when three children are available. Keep
   them occupied with independent children. The coordinator reviews returned
   work while freed editors take the next assignments. Use direct agent waits,
   not a heartbeat, scheduled task or visible Codex chat per short.
7. Review **story and design first**, before polishing technical details.
   Compare the moving result with the relevant reference traits. Return one
   consolidated repair packet, not a series of single-word nitpicks.
8. Export reviewed EDLs, verify the actual final files and branding, and write
   the delivery manifest. Every selected ID must end exported or as a visible
   exception with the next action. Never hide a weak/failed short in the total.

## Efficiency that protects quality

Use at most three Valmera requests/jobs in flight. One child's mutations are
serial; independent children may proceed. Reconcile a lost response by job ID
and EDL version before retrying. Use `apply_edit_batch` for related, already
planned changes; its timestamps describe the resulting timeline.

A normal child needs one source inspection, a deliberate editing pass, one
candidate render and one shared inspection packet. A real defect may require a
repair render. Do not generate hundreds of frames, rebuild the media locally,
re-index unchanged speech, install another ASR pipeline, or repeatedly compare
PCM just to make a report look rigorous. Use a specialized measurement only
when it answers a timestamped unresolved defect. Reuse checksum-matched
information; after a scoped repair recheck the changed window and boundaries.

Two editor repair rounds and one coordinator rescue are the ceiling, not a
routine. If the tool prevents the intended edit, report the concrete capability
gap; do not spend hours hiding it in a custom re-encode/transcription pipeline.

## Completion

Do not claim viral performance or a guaranteed view count. Report what was
actually produced, the strongest story/design decisions, observed defects and
exceptions, elapsed time, render/re-index counts, and the verified local
manifest. Quality is judged from the result, not a self-awarded score.
