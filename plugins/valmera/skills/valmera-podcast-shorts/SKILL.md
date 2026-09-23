---
name: valmera-podcast-shorts
description: Reliably turn a podcast, interview, or other long-form video into story-led shorts in the user's chosen format with Valmera. Use when Codex must acquire or open a source, calibrate editing taste from references or pilot cuts, select coherent stories, run a bounded parallel editor pool, independently quality-check every render, export approved finals, and hand a verified export manifest to a downstream publisher or CRM.
---

# Valmera Podcast Shorts

Run this as a finite production pipeline, not an open-ended swarm. The
coordinator owns story selection, taste, assignments, final QC, export, and the
truthful final report. Valmera performs project-scoped edits and renders. Each
subagent owns one child project at a time.

Use workflow version `valmera-podcast-shorts-v7`.

## Read the v7 contracts

Read these files completely before acting:

- `references/orchestration-v7.md` before creating a run or launching agents.
- `references/taste-v7.md` before choosing references or editing styles.
- `references/editor-v7.md` before selecting stories or assigning an editor.
- `references/qc-v7.md` before accepting or repairing a candidate.
- `references/export-v7.md` before requesting or downloading final exports.
- `references/delivery-quality-v7.md` before source acquisition, typography,
  and approval; it defines the current brief's source/detail floor, stable
  captions with active-word color, and opt-in durable delivery quality gate.

When the brief requests fast conversational B-roll, a spoken hook followed by
a silent montage, a silent action opening, or persistent-headline conversation,
also read `references/style-lanes-v7.md` before selection, assignment, and QC. The user's
current brief determines structure, pacing, intentional silence, duration,
aspect ratio, and rounded-card treatment. Generic defaults and earlier taste
profiles must not override those choices. Rounded corners are a composition
treatment, not an aspect ratio.

The user's September 9 headline references and current defaults are documented
in `references/headline-conversation-references-v7.md`. Read it when using
`headline-conversation` or the user's current four-style brief. Its black-canvas
layout supersedes the earlier all-shorts rounded-card default. Four styles
still use at most three reusable editors.
For that brief, the September 14 corrections in `style-lanes-v7.md` remove
the default editing lane, require bold headline hierarchy, and preserve both
Valmera brand elements. The canvas default remains. Apply these corrections
even when an older starter calls Style 4 the default or bans watermarks.
Use the admin page's native watermark placement choices as described there;
the user's correction authorizes that route. Do not invent an exact-corner
blocker or stop the editing pool for routine placement adjustments.

The older unversioned contracts, schemas, `run_registry.py`, and
`validate_contract.py` describe the retired v6 workflow. Do not load or run
them for a v7 run. They remain only so an interrupted v6 run can be audited.

## Non-negotiable operating rules

1. Use exactly one coordinator and at most three editor subagents. Reuse those
   editors for later shorts; do not create a visible Codex task per short.
2. Allow at most three Valmera requests in flight across the run. Calls within
   one child project are sequential. A returned job is in flight until it is
   terminal, even while `wait_for_job` is not being called.
3. Never create a scheduled automation or heartbeat to continue production.
   Stay in the current turn, wait for subagents directly, and resume as soon as
   one returns. Do not repeatedly poll unchanged state.
4. Never make an agent wait merely because another child is editing. Only the
   three-request capacity limit, a same-project call, or a real dependency may
   block it.
5. Do not use `create_thread`, `edit_shorts`, or Valmera's in-house agent.
   Use collaboration subagents and direct project-scoped editor tools.
6. Do not use the retired global lease/call-permit registry. Track only durable
   stage and child outcomes with `scripts/run_state.py`; Valmera's queue and
   job IDs track requests.
7. A child may return a `candidate`, never a self-approved final. Only the
   coordinator can mark `ready`, and only after reviewing the exact preview
   for the exact current EDL version.
8. Do not silently omit a selected short. Every selected ID must finish as
   `exported`, `needs_user_review`, or `failed_technical`, with evidence and a
   specific next action.
9. Do not say “all good,” “ready,” “finished,” or equivalent before all
   applicable QC and export checks have passed.
10. Keep source media, references, QA artifacts, and exports under the run
    directory. Do not inspect or modify a downstream CRM; write its handoff
    manifest only.
11. Every final filename begins with its reviewed style-lane ID and `__`;
    reject incidental date/place subheadlines and indirectly related montage
    shots during coordinator QC. The detailed gates are in
    `references/style-lanes-v7.md`, `references/qc-v7.md`, and
    `references/export-v7.md`.
12. Every spoken caption treatment must visibly color the word being spoken,
    normally inside a three-to-four-word group. No B-roll visual may repeat
    within one short. For `hook-to-silent-montage`, the editorial program is
    at most 25 seconds and the post-hook montage is at most 15 seconds; the
    separate native Valmera ending is excluded from those limits.

## Create the run

Use a project-local directory:

```text
<repo>/.tmp/valmera-podcast-shorts/<run-id>/
  run.json
  source/
  taste/
  assignments/
  candidates/<short-id>/
  exports/
```

Reusable approved profiles live at
`<workspace>/.valmera/podcast-shorts/taste-profiles/<profile-id>.json`. Copy the
selected profile into the run's `taste/` directory so the run remains
reproducible. Do not overwrite a user-approved reusable profile unless the user
asks to revise it.

Initialize it once:

```bash
python <skill-root>/scripts/run_state.py init \
  --run-dir <run-dir> --run-id <run-id> --source <source-or-topic>
```

For new runs under the user's current four-style brief, also supply
`--quality-policy <run-dir>/taste/quality-policy.json` using
`references/delivery-quality-v7.md`. Approve one actual native final before
batch editing; small selection proxies never establish delivery quality.

On resume, run `status`, reconcile only recorded nonterminal Valmera job IDs,
and continue the next unfinished stage. Never infer progress from task prose.
At the start of each stage, advance exactly once with `run_state.py phase
--run-dir <run-dir> --stage <stage>`; the state machine rejects skips and
backward transitions.

## Calibrate taste before the batch

Taste cannot be communicated reliably by adjectives alone. Accept any useful
number of user-liked short-form examples plus optional disliked examples; do
not impose a reference quota. One precise example can teach one trait, while a
larger varied set can reveal stable taste. Follow `references/taste-v7.md` for
the exact intake and inspection contract. Analyze which exact properties
transfer: hook construction, cut density, caption behavior, framing, B-roll
logic, graphic language, sound, color, and ending. Do not copy protected
footage, audio, branding, or a creator-specific identity.

Persist `taste/taste-profile.json`, then attach its exact checksum to the run:

```bash
python <skill-root>/scripts/run_state.py taste \
  --run-dir <run-dir> --profile <run-dir>/taste/taste-profile.json
```

Separate:

- invariants shared by every short;
- two to five approved style lanes used for variety;
- forbidden patterns;
- reference-specific observations and confidence.

If there is no approved taste profile and the user supplied no examples, do
not spend the whole batch guessing. Build three visibly different pilot
treatments of one strong story, export low-resolution previews, and ask the
user to choose or combine them. This is the only intentional taste-calibration
pause. The user may explicitly choose `autopilot`, in which case record that
the coordinator selected the profile without user calibration.

## Acquire, understand, and select

If given a topic, find one suitable long-form source. If given a URL or an
existing Valmera project, use that source. Hash downloaded media and do not
redownload or reupload an identical file within the same run.

Read the full transcript before selecting. Select complete micro-stories, not
isolated quotes. Each must have an immediate hook, enough context to understand
the claim, development or evidence, and a payoff. Preserve meaning and speaker
intent. Reject filler, duplicate lessons, weak endings, and clips that need
outside context.

Apply completeness to the assigned structure. A hook-to-montage short needs a
self-contained spoken premise and a relevant visual payoff; it does not need
continued speech during the montage. An action-opening short can begin with
silent footage of the actual subject. Never extend or restore dialogue just
to satisfy a generic spoken-story template.

Select all and only the independently strong stories. There is no target,
minimum, maximum, or default clip count: five and fifty are both valid, and
zero is valid when nothing clears the bar. Stop only when every remaining
candidate fails the complete-micro-story and non-duplication tests. Never pad,
merge weak moments, split one idea merely to raise the count, or omit a strong
distinct story merely to lower it. Execution stays bounded by processing the
selected queue in three-editor waves; editorial selection does not.

For each story, write a compact assignment containing immutable parent/child
IDs, source range, verbatim transcript, story beats, hook/payoff, intended
audience, one style lane, reference observations to adapt, forbidden choices,
and acceptance risks. Include the output ratio, frame treatment, allowed
duration, pacing, planned audio states, and transition cues. Materialize all
approved ranges with one explicit `make_shorts(project_id, clips=[...])` call,
then record each returned child ID with `run_state.py add-short`. Store the
assignment as JSON with a lowercase slug in `style_lane`; `run_state.py`
carries that reviewed lane through candidate, QC, export filename validation,
and the handoff manifest.

For the current four-style brief, first record each story's selection reason
and closest alternative and review the batch for default-lane bias, using
`style-lanes-v7.md`. No fixed distribution is required. Include the headline
weight and required Valmera branding geometry/runtime in assignments.

## Edit with a three-worker pool

Spawn no more than three editor subagents. Give each the editor and QC
contracts plus exactly one assignment. An editor must:

- open only its immutable child project;
- inspect source words and visuals before mutating;
- build the story in a small number of deliberate editing passes;
- render one rough preview when composition is established;
- consolidate findings into one repair pass;
- render one current candidate preview;
- save a local candidate evidence bundle and return its path, EDL version, and
  outstanding job IDs.

When an editor returns, the coordinator reviews that candidate immediately.
If it passes, assign the same idle editor the next queued short. If it fails,
send one timestamped, consolidated repair packet to the same editor. Do not
create a replacement agent for ordinary repair work.

Use direct agent waits, preferably a long bounded wait that wakes when any
editor finishes. Never schedule a future wakeup. If a tool or agent genuinely
needs user action, state the exact blocker instead of pretending it is still
working.

## Accept, repair, and export

Apply `references/qc-v7.md` independently to every candidate. Verify story and
taste before pixel polish. Use adaptive evidence rather than generating every
frame of every render: inspect the opening, every shot/overlay/caption boundary,
every story beat, the payoff, the final tail, regular coverage through the
remaining timeline, and the complete speech/caption stream. Increase density
only around a suspected defect.

Allow at most two editor repair rounds. If a candidate still misses a blocker,
the coordinator performs one bounded rescue pass or marks it
`needs_user_review`; it must not loop forever or vanish from the batch.

Export only `ready` children and only the reviewed EDL version. Queue no more
than three finals concurrently. Download each finished final, verify identity,
duration, dimensions, audio/video streams, EDL version, and checksum, then
record it with `run_state.py export`. Create `exports/manifest.json` only when
the set of terminal children is complete. Follow `references/export-v7.md` for
the exact boundary when MCP export is unavailable.

## Finish truthfully

Before reporting completion, run:

```bash
python <skill-root>/scripts/run_state.py status --run-dir <run-dir> --json
python <skill-root>/scripts/run_state.py finalize --run-dir <run-dir>
```

Report counts for selected, exported, needs-user-review, and failed-technical;
list every non-exported short and its next action. A run with exceptions is
complete as an audited production attempt, not “all shorts delivered.”
