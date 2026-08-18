---
name: valmera-podcast-shorts
description: Orchestrate topic-to-shorts production with parent-owned YouTube source discovery, a durable duplicate-video ledger, Valmera upload/indexing, explicit coordinator-authored story arcs, one related viral style reference, story-specific editorial treatments, one user-visible Codex task per child, and independent full-render parent QC. Use when a user supplies a topic and wants Codex to find a new long-form source, turn every qualifying story into a vertical short, manage visible editors, repair failures, and verify every final edit.
---

# Valmera Podcast Shorts

Turn a topic into quality-controlled story shorts without
delegating creative judgment to Valmera's in-house editing agent. Treat the
coordinator as the owner of source acquisition, the parent board, story
selection, treatment direction, assignment, and final acceptance. Give each
child project exactly one sidebar-visible, user-owned Codex editor task.

## Load the contracts

Read the following files completely before the corresponding stage:

- Read `references/run-state-contract.md` before creating a run, creating a
  visible task, or making any Valmera call.
- Read `references/valmera-contracts.md` before any Valmera mutation.
- Read `references/topic-source-contract.md` before discovering, reserving,
  downloading, or uploading any topic-selected source/reference video.
- Read `references/selector-contract.md` before selecting stories or spawning
  any read-only evidence helper.
- Read `references/reference-research-contract.md` before studying a style
  reference.
- Read `references/editorial-treatment-contract.md` before assigning any child.
- Require every child editor to read `references/run-state-contract.md`,
  `references/valmera-contracts.md`, `references/editor-contract.md`, and
  `references/acceptance-contract.md` completely before editing.
- Read `references/run-state-contract.md` and
  `references/coordinator-qc-contract.md` before accepting any completed child
  or advancing the editor queue.

Use prompt version `valmera-shorts-editor-v6`. Keep the static editor contract
on disk; pass dynamic project/story data separately as JSON.

## Validate every machine contract

The files under `schemas/` are normative; prose JSON blocks are readable
examples only. Persist each exact artifact in the durable run state and run
`scripts/validate_contract.py` before it can drive the next stage. A validation
error fails closed and must be repaired; never ask another task to infer a
malformed payload.

Use these schema/lineage checks:

- `selection` after coordinator approval;
- `reference-profile` only after the complete preselection YouTube sampled-
  visual evidence, exhausted available captions/transcript, and full-stream
  local ASR processing,
  final coordinator selection, and the separate post-upload Valmera sampled-
  visual verification;
- `acquisition-record --against <selection.json> --against
  <reference-profile.json>` before story materialization (omit the reference
  profile only on a valid abstained path, where no reference profile exists);
- `child-assignment --against <selection.json> --against
  <acquisition-record.json> --against <reference-profile.json>` before task
  dispatch. Supply the run's reference profile even when that story records an
  evidence-backed `inapplicable` decision; inapplicability is a child treatment
  choice, not missing lineage;
- `pre-mutation-recast --against <child-assignment.json>` before and after the
  coordinator records its approval or block decision;
- `editor-result --against <child-assignment.json>` and, for an ambiguous
  assignment, `--against <approved-recast.json>` before parent QC;
- `parent-qc --against <editor-result.json>` before accepting/repairing a child;
- `coordinator-run-result --against <selection.json> --against
  <acquisition-record.json> --against <reference-profile.json>` before final
  reporting on a post-selection run. Use the schema's explicit
  blocked-before-selection branch only when those upstreams genuinely do not
  exist.

Invoke the validator as an argument vector, for example
`<skill-root>/scripts/validate_contract.py --schema child-assignment --input
<assignment.json> --against <selection.json> --against
<acquisition-record.json> --against <reference-profile.json>`. `--against` is
repeatable and the validator detects each upstream artifact type. It
automatically selects a local Python with `jsonschema` or exits with actionable
setup guidance. Do not parse a task's surrounding prose as the artifact;
require the JSON object itself.

## Fence every Valmera call and checkpoint

Use only the canonical SQLite control plane and exact protocol in
`references/run-state-contract.md`. Initialize it, create the durable run, and
recover `resume` plus `audit` before any resumed work. Do not pass an alternate
registry path in a real run.

Grant, snapshot, and close the monotonic `acquisition`, `selection`,
`reference`, `acquisition_record`, and `materialization` coordinator phases.
Freeze the validated acquisition record before granting materialization; store
the complete `make_shorts` outcome separately. Register each resolved visible
task and its validated assignment before granting that child an edit lease.

Immediately before every Valmera request—including reads, uploads, visual inspections,
audits, renders, mutations, and `wait_for_job`—the exact holder must atomically
obtain the appropriate `begin-run-call` or `begin-call` permit. Make exactly
one request, then use the matching `end-run-call` or `end-call` with the
authoritative outstanding job IDs. `check-run-lease` and `check-lease` are
diagnostic only and never authorize a request. Never close, rotate, or grant a
new lease while a call permit is in flight. If the transport result is
ambiguous, retain the permit and lease and follow the contract's reconciliation
path; never guess an empty job list.

## Keep the roles separate

- The coordinator discovers and acquires the long source and reference,
  uploads them, visually inspects the full source, authors every story range
  and treatment, calls `make_shorts`, assigns children, visually inspects every
  finished render, and sends repairs.
- Valmera stores, indexes, exposes evidence, mechanically materializes only the
  coordinator's explicit ranges, and renders direct EDL edits. It must not
  select stories or perform creative editing.
- Each visible child task edits exactly one immutable child project. It must not
  choose source stories, mutate the parent, or edit a sibling.

Codex does not natively perceive continuous video playback or audio. For each
final short, exhaust its shot/EDL pages, keep adjacent sampled timestamps at
most 1.0 second apart, and inspect every enumerated shot boundary, B-roll
junction/replacement, opening, payoff, and final tail. For a reference candidate
or uploaded reference asset, sample its full duration at gaps of at most 1.0
second and reinspect every boundary/cut/transition actually observed in those
samples. Public `look_at_asset` exposes neither an exact cut inventory nor
paginated storyboard, so never claim all reference pages or unknown boundaries
were exhausted. Record this as sampled visual evidence, never as every-frame
perception.

Automatically generated visual, story, or audio critic output is a lead, not a
creative verdict or evidence source. Codex must decide from raw pixels,
transcripts, reliable metadata, EDL facts, and permitted measurements. Override
an automated finding only with cited direct evidence; if a backend veto cannot
be overridden, report a product blocker. Never copy a critic's subjective audio
conclusion into a v6 artifact.

## Use references safely

The coordinator must find, acquire, upload, and study one strong related
short-form reference for the entire run. First cover every candidate's full
visual timeline using the dense-frame rule above, exhaust its available
captions/transcript, and record any visible music/sound label. A candidate may
then be provisionally reserved and downloaded only to hash/probe it, process
its full audio stream with local ASR, retain detected speech/lyrics and warnings,
and obtain deterministic aggregate audio measurements before the coordinator's
final selection. Upload only the final
selection; release a rejected provisional reservation and remove its temporary
bytes. After upload, separately apply the dense-frame rule to the protected
Valmera asset and verify its transcript/identity. Do not make child editors browse
independently. Follow
`references/topic-source-contract.md` for acquisition and
`references/reference-research-contract.md` for analysis, then apply
`references/editorial-treatment-contract.md` separately to every story.
Reference popularity never means every child should copy its treatment and
never proves that its footage or soundtrack is licensed for reuse. Codex is the
sole creative decision-maker and must not report direct audio perception.
Music fit is an explicitly labeled inference from story/transcript, reliable
track identity/source metadata, exposed source-track tempo/beat-sample/energy-
landmark facts, exact EDL
timing/offset/loop/gain/ducking/fades, and available aggregate whole-mix LUFS/
true-peak/LRA/silence facts. Do not call a
separate model or `review_audio` to make the decision. If evidence is too weak,
record the identity as unknown where applicable and choose no music.

## Require the topic-driven entry mode

Discover a new long-form YouTube source from the user's topic, reject IDs
already present in the durable ledger, reserve before download, upload it as
one `kind="shorts"` parent, select stories, and attach one related
`shorts_reference` before child creation. Resume only the same durable run ID
through its registry and frozen artifacts.

This v6 workflow does not adopt an arbitrary attachment, pre-existing Shorts
board, or standalone child. Those inputs lack the topic run's immutable
YouTube/ledger/reference lineage. Stop without mutation instead of fabricating
IDs or bypassing the global call fence; use a separately designed adoption
workflow when one exists.

## Preflight the connector

Always require `open_short(child_project_id=...)`, the normal direct editing
tools for child work, and an active Valmera `make_shorts` schema that accepts an
explicit `clips` array. Never call `edit_shorts` or `export_final`.

Topic mode additionally requires the direct upload contract to preserve
`upload_finish(kind="clip", role="shorts_reference", duration_s=...)`. Stop
before child creation if the role is missing; uploading the viral reference as
an ordinary B-roll clip would violate the timeline boundary and would not
reliably propagate it to children.

If `make_shorts` lacks `clips`, or if the catalog still exposes `edit_shorts`
or `export_final`, the task has stale Valmera metadata. The optional presence
of `count` is not itself stale; the current contract is
`make_shorts(project_id, clips, count?, style_note?)`. Stop before any new
selection/planning mutation and identify the metadata channel before giving a
recovery instruction:

- For a developer-mode MCP connection, deploy/restart the server, select
  **Refresh** on that connection in ChatGPT Plugins, confirm the advertised
  schema changed, and only then start a new Codex task.
- For a packaged/private/published remote plugin, including a reference ending
  in `@created-by-me-remote`, reconnecting or starting a new task is not enough.
  Scan the live MCP server, submit and publish a new plugin version, then
  refresh/reconnect and start a new task.

Never tell the user to reconnect or start another task until the metadata
itself has been refreshed or a new release has been published. Do not fall
back to Valmera-authored selection. This stale planner schema does not block
direct editing of an already resolved child project.

## Run the parent workflow

1. Establish a stable `run_id` and follow
   `references/topic-source-contract.md`: rank long-form candidates, atomically
   reserve an unused YouTube ID, download and verify the complete selected
   source, create one parent, upload it as `original`, index it, and commit the
   ID to the durable ledger. On resume, recover the exact same run, parent, and
   frozen phase artifacts from the durable registries.
2. The coordinator—not Valmera and not a child—must follow
   `references/selector-contract.md` personally. Exhaust the long source's shot
   pages, inspect timestamped frames across every shot and candidate window,
   exhaust the ASR output and its warnings, and freeze the strict selection JSON.
   Read-only helpers
   may gather evidence but may not approve ranges.
3. Permit zero clips when no arc passes. Validate every coordinator-authored
   clip for source sentence boundaries, 10-120 seconds, no overlap, distinct
   editorial value, and complete setup/development/payoff. Do not impose an
   arbitrary count quota; source duration, non-overlap, score threshold, and
   story completeness are the bounds. If the frozen `clips` array is empty,
   persist the schema-valid abstained acquisition record and coordinator result,
   then finalize an honest zero-child `abstained` run. Do not acquire a
   reference, grant reference/materialization, call `make_shorts`, or let any
   fallback planner run.
4. In a nonempty topic run, only after story arcs are frozen, shortlist related
   strong short-form references. Before provisional reservation, inspect the
   candidate's ordinary-player timeline from 0.00 to full duration with sampled
   timestamps at most 1.0 second apart plus every shot/cut boundary, read all
   available captions/transcript, and
   record any visible music identity. Provisionally reserve and download the
   candidate, verify/hash/probe it, process the full audio stream with local ASR,
   retain all detected speech/lyrics and warnings, and compute deterministic
   aggregate audio measurements. Codex then makes the final
   selection from those facts and visual evidence. Release and delete a
   rejected candidate; never upload it. Upload only the final selection to the
   parent with `kind="clip", role="shorts_reference"`, wait for its
   perception/index job, verify the role, commit its ID to the ledger, then
   separately apply the dense-frame reference schedule, inspect every boundary
   observed there, and verify its identity/transcript. Do not claim an exhaustive
   shot/page/transition inventory. Extract the abstract profile in
   `references/reference-research-contract.md`.
5. For a nonempty selection, validate and freeze the exact acquisition record,
   then call
   `make_shorts(project_id, clips=[...], style_note=...)` exactly once with
   the complete frozen selection, even when it contains more than eight clips.
   The call permits mechanical word-boundary materialization only; it does not
   authorize Valmera selection or editing. Poll the returned job or
   `shorts_status`; never repeat the logical operation after a timeout.
6. Wait until every successful card has an immutable child project ID. Compare
   each seeded child range with its approved parent range and investigate any
   change beyond ordinary word-boundary snapping. In reference mode, verify the
   `shorts_reference` is available to every child and remains reference-only.
   Reconcile the complete frozen selection one-to-one against board cards by
   selection fingerprint, order, and range. Every selected arc must be recorded
   as `generated`, `pending`, or `materialization_failed`; no selected arc may
   disappear merely because a terminal/partial planner job returned fewer
   children. Resolve a still-running job from its durable ID. For a terminal
   missing child, record the exact failed arc and block that item without
   blindly reissuing `make_shorts`.

## Create visible child editor tasks

Before creating a task, follow `references/editorial-treatment-contract.md` for
that exact story. Infer its narrative mode, audience promise, emotional
trajectory, evidence needs, speaker strength, pace, and source-picture fitness.
Then decide what this story should transfer, adapt, or reject from the shared
reference. Do not turn the reference or topic into one preset for all siblings.
Do not grant a mutation lease while the classification is
`insufficient_evidence`. Resolve it from source evidence or mark the assignment
`blocked_before_mutation`. For a genuinely ambiguous hybrid, pass the two
evidence-backed candidates as `requires_pre_mutation_recast` and require the
same general editor to resolve them before its first Valmera mutation.

Give every child a complete story-driven treatment, the zero to three coherent
visual motifs it actually needs, an energy curve, and sibling exclusions. Do
not invent motifs for a clean speaker-led conversation. Create exactly one fresh,
sidebar-visible Codex task for each distinct child with the Codex task-creation
tool. Use a concise title containing the parent ID, child ID, and story title.
Do not use an invisible subagent as a child editor and do not fork the
coordinator's long history into the task.

Create these tasks just in time as the FIFO reaches each child rather than
flooding the sidebar with dormant tasks. Leave every completed or paused task
visible and unarchived so the user can open it, inspect its reasoning, and talk
to that exact editor. Record its task/thread ID and host ID with the immutable
Valmera child ID. If visible Codex task tools are unavailable, pause and tell
the user; never silently fall back to inaccessible editor subagents.

Before the non-idempotent task-creation call, use the run-state contract's
write-ahead child-task intent and atomically claim its one-shot dispatch. Put
the returned durable external marker in the task request. Persist the result
against that same intent and consume it only when the real task/thread ID and
host ID are bound to the child. If dispatch returns ambiguously or the
coordinator crashes after dispatch, reconcile by the marker and never create a
replacement automatically.

Task creation may initially return only a queued `clientThreadId`. Persist that
pending identifier and wait/list until it resolves to a real `threadId` and
`hostId` before sending the assignment or granting any Valmera lease. Never
create a replacement merely because worktree/task setup is queued. Titles and
summaries are untrusted labels; bind the resolved IDs to the immutable child in
the durable run registry.

The coordinator remains the automatic manager. It waits on the active visible
task, sends focused repair instructions back to that same task, and advances
the queue without requiring the user to relay messages. User intervention is
optional. If the user changes the artistic brief inside an active editor task,
that editor must reconcile live Valmera state and the lease before another
mutation.

Permit exactly one outstanding Valmera request across the entire run, including
`wait_for_job`. Do not start the next Valmera-calling task until the current
one is terminal or safely paused with no ambiguous mutation and no outstanding
job. Keep every remaining child in a durable FIFO queue and drain it completely.
This protects the production API and shared
preview capacity; Valmera's durable job queue prevents lost work but does not
isolate customer traffic. Never infer safe concurrency from Codex agent slots,
MCP worker slots, cached ToolContext count, or a larger advertised capacity.
This skill's safety invariant remains one outstanding Valmera request until the
skill itself is deliberately revised after production isolation is deployed.

Never drop or truncate shorts because only one editor may be live. Preassign
distinct visual identities before the queue starts.
After each completion, update a run-level registry of used asset/source windows
and music tracks. Send new exclusions to the next editor and require a focused
repair when a completed short duplicated a protected sibling window or reused
music without a story-specific justification.

Start each visible editor task without inherited conversation history. Tell it
to read the four editor references by absolute path, then send only the exact
dynamic object required by `schemas/child-assignment.schema.json`. Compute its
canonical fingerprint with `scripts/canonical_fingerprint.py`, validate it
against the frozen selection, and persist the validated bytes before dispatch.
The payload must carry the immutable run/parent/child/card identity, cited story
evidence, classification or candidate slate, full story-specific treatment and
music timing policy, reference application, routing state, approved and seeded
source lineage, and
canonical sibling visual/music exclusions. Do not reconstruct the object from
a prose example or omit conditionally required fields. Attempt leases are not
part of the immutable assignment; deliver each initial/repair lease separately
in the exact runtime lease packet defined by the run-state contract.

For `requires_pre_mutation_recast`, require the task's first response to
validate against `schemas/pre-mutation-recast.schema.json`. The coordinator
compares its cited story evidence to the candidate slate and sends exactly one
of:

- `RECAST_APPROVED` with the same assignment ID/input fingerprint, the approved
  canonical narrative cast, and exact treatment delta; or
- `RECAST_REJECTED` with the same identifiers and the precise evidence still
  unresolved, followed by a revised approval or `blocked_before_mutation`.

This handshake is lease-free. Persist its finalized schema-valid approval
before issuing the first edit lease. No Valmera mutation may precede approval,
and the editor must not substitute a normal terminal acceptance result for this
handshake.

The editor must work only on `child_project_id`, pass it explicitly to every
project-scoped tool, apply the dense-frame rule to the child and inherited
reference, exhaust their speech transcripts/captions, apply the assigned
treatment with evidence-based adaptation, directly
edit the EDL, wrap every Valmera request in its exact atomic begin/end-call
permit, and return only the result schema in
`references/acceptance-contract.md`, including the current runtime attempt's
lease ID/generation and an empty `outstanding_job_ids` array. That lease
identity belongs to the attempt packet, never the immutable assignment. The
coordinator must use task waiting/reading tools
to validate the result and send repairs to the same visible task. Never create
a replacement writer for a child merely because its first attempt needs repair.

## Run mandatory parent QC after the child finishes

Do not start parent QC while the child is editing. Wait until the visible task
returns its acceptance JSON with `outstanding_job_ids: []`, then freeze that
task's mutation lease, grant the coordinator's QC lease, and follow
`references/coordinator-qc-contract.md`. Wrap every parent Valmera inspection
request in that QC lease's exact begin/end-call permit.

The coordinator must independently open the exact child, compare live EDL and
preview versions, exhaust its shot/EDL pages, and inspect rendered frames from
0.00 to program end at gaps of at most 1.0 second plus every edit boundary and
required key moment. Record exact sampled timestamps and any evidence gaps.
Under its QC lease, materialize the untouched, complete current preview and
independently run `scripts/transcribe_candidate.py` over its full audio stream.
Parent `pass` requires `program_speech_transcript.source=render_asr`, no
processing gaps, and `render_edl_version` equal to both live and preview EDL
versions. It also requires the Valmera-authored preview provenance flag
`audio_model_review=false`; missing or true blocks acceptance, and no agent may
set or override it. The kept/source transcript is planning context only and
cannot satisfy this current-render gate. Exhaust its ASR output and warnings, the kept
transcript, caption pages, and deterministic audio audits, and verify any
music decision from track identity/license, exposed source-track tempo/beat-
sample/energy-landmark facts,
exact EDL timing/offset/loop/gain/ducking/fades, and aggregate preview LUFS/
peak/silence audit facts. Label music
fit as a Codex inference with rationale and confidence; never report it as
direct sensory evidence. Judge the edit against its own story profile, treatment,
and reference transfer/adapt/reject map. Never accept
a child solely because its own report says `ready` or because it superficially
resembles the shared reference.

If parent QC fails, send one timestamped repair packet to the same visible task,
grant that task the next repair lease, wait for its new terminal result, and
repeat parent QC against the new current render. Do not create a replacement
editor or start the next child between repair rounds. Permit at most two focused
parent repair rounds; after that, return `blocked` with the unresolved evidence.
Complete the full parent visual pass first and consolidate all known defects into that
one packet; do not create avoidable back-and-forth with partial repair messages.

After the repair cap, first require a safe checkpoint with no active,
outstanding, or ambiguous Valmera job. Freeze that child task, persist its
`blocked` evidence, include its actual asset/music usage in sibling exclusions,
release its mutation lease, and continue the FIFO. It remains not-ready and
contributes to the final `partial` or `blocked` result; one failed child must not
strand every later child.

The next FIFO item starts only after parent QC returns `pass` for the exact live
EDL/preview version or the current child reaches that explicit safe-blocked
checkpoint. Parent inspection calls share the same one-request Valmera
concurrency budget as editor calls.

## Resume safely

- Run `run_registry.py resume --run-id <run-id>` and `run_registry.py audit`
  before any Valmera request, task creation, or lease grant. Reconcile any live
  lease or in-flight call permit with its exact recorded holder first.
- Reuse the original `run_id`. Reconcile source/reference reservations in the
  durable ledger before any new download. Never select a replacement merely
  because a committed upload or indexing response was lost.
- Treat Valmera state as authoritative. Read `project_state`, `get_edl`, and
  `shorts_status` before any resumed mutation.
- Never call `make_shorts` while a planner job exists or children already exist.
- Never blindly retry a mutating tool after an ambiguous timeout. Poll its job
  or reread the EDL and assets first.
- Never run two editors on the same child or let Studio's Edit agent work on a
  child concurrently with Codex.
- Resume the same visible task after a crash or repair request. Before granting
  another Valmera lease, verify the former task has no active or ambiguous job.
- Skip a child only when its latest preview matches its live EDL, its stored
  child result satisfies every acceptance gate, and stored parent QC passes for
  that exact version.

## Finish

Drain the complete assignment queue. Reconcile every returned child ID and EDL
version against Valmera. Report the topic/run ID, committed source video ID,
nullable reference video ID/reason, parent ID, selection fingerprint, and
`ready_for_studio_export`, `partial`, `blocked`, or `abstained`. Include one row
for every frozen selected arc—not only generated child IDs—with its
materialization status, nullable child ID, treatment, reference adaptation,
parent-QC version/status, and exact failed gates. The row count must equal the
frozen selection count. `abstained` requires zero selected arcs, zero children,
a null reference video ID, and an `abstain_reason` that records
`not_acquired_zero_selection`. Never count a child as ready without parent-QC
`pass`.

Final export is deliberately Studio-only. Tell the user to export the verified
children in Valmera Studio. Do not claim the built-in end-card music tail was
verified from an ordinary preview. Ordinary preview evidence proves only that
the EDL is `tail_eligible` and renderer carry is `configured`. When a current
final is available, a deterministic whole-mix waveform/LUFS/peak/silence audit
may record aggregate signal across the program/CTA boundary and complete card,
but cannot isolate the music bed. Describe track suitability only as a Codex
inference from the permitted evidence.

Validate and persist the coordinator result, then call
`run_registry.py finalize-run` only with the status allowed by the frozen
selection/materialization and every parent-QC result. Never finalize while a
lease or call permit is live.
