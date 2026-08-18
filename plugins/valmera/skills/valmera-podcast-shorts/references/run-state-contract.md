# Run, Task, and Valmera Lease Contract

Use `scripts/run_registry.py` as the durable control plane for every topic run.
It persists coordinator phases, frozen story/materialization evidence, visible
Codex task ownership, editor and parent-QC attempts, repairs, and the single
Valmera production slot. This protocol is fail closed.

## Contents

- Canonical registry and trust boundary
- Durable identity and state
- Coordinator phase protocol
- Frozen selection, binding, and materialization
- Visible child registration
- Child edit and parent-QC protocol
- Repairs, pausing, and restart recovery
- Finalization
- Idempotency and failure handling
- Checkpoint compatibility

## Canonical registry and trust boundary

Use only the default production registry:

```text
~/.codex/state/valmera-podcast-shorts/run-registry.sqlite3
```

Initialize it once:

```bash
python3 '<skill-root>/scripts/run_registry.py' init
```

Do not pass `--registry` in production. That option is for isolated tests and
explicit recovery only. A second database creates a second lease domain and
defeats the one-call-global rule.

The current database schema is version 4. Every command except `init` refuses a
missing file, an old v1/v2 file, a corrupt database, or an unknown application ID.
`init` recognizes only the canonical empty v3 layout: it makes and verifies an
integrity-checked v3 backup, migrates transactionally to v4, and then validates
the v4 database. A nonempty or shape-mismatched v3 database is refused.
Never delete, replace, or silently recreate the database to clear a busy lease.

The registry is a cooperative local fence. Valmera does not yet validate these
lease tokens server-side, so the registry cannot stop code that deliberately
bypasses this protocol. Every Valmera call must be enclosed by an atomic
`begin-*-call`/`end-*-call` permit. Close refuses while a permit is in flight.
The read-only `check-*` commands are diagnostics only and never authorize a
production call. Server-side validation is still required to stop a process
that deliberately ignores the local permit protocol.

## Durable identity and state

The registry stores:

- run ID, topic, coordinator Codex `thread_id` and `host_id`;
- frozen parent project ID and exact selection fingerprint;
- validated coordinator phase snapshots and their trusted SHA-256 digests;
- the immutable write-ahead visible-task intent, one-shot creation claim,
  nullable queued `clientThreadId`, resolved real task/host IDs, and consumption
  state for every materialized child;
- each child project/card/title mapped to exactly one visible Codex
  `thread_id`, `host_id`, and exact validated immutable assignment JSON/digest;
- the exact ambiguous recast assessment input, its later exact coordinator
  approval/block decision, their validated digests, and selected candidate;
- every never-reused lease ID and monotonically increasing generation;
- child/QC status, repair round, full current exclusions, and immutable attempt
  history containing the validated result JSON and digest;
- the exact validated coordinator run-result JSON/digest required before a
  terminal status;
- all frozen/released/revoked leases and the single global live lease.

Only a `live` lease authorizes Valmera. A `frozen`, `released`, or `revoked`
lease never becomes live again. Resumption uses a new lease ID and a higher
generation, preventing ABA authorization by a stale holder.

SQLite enforces at most one live lease and one in-flight Valmera call across
coordinator acquisition, selection, reference, acquisition-record,
materialization, child edit/repair, and parent QC for all runs in this database.

## Coordinator phase protocol

Create the run before any Valmera work:

```bash
python3 '<skill-root>/scripts/run_registry.py' create-run \
  --run-id '<run-id>' --topic '<topic>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --op-id '<run-id>:create'
```

Coordinator phases are monotonic:

1. `acquisition` — fetch/upload/index the source and persist its source
   acquisition checkpoint.
2. `selection` — read/watch all required evidence and persist the exact valid
   `selection` artifact.
3. bind the immutable parent and selection.
4. `reference` — acquire/index/watch the chosen reference and persist the exact
   valid `reference-profile` artifact.
5. `acquisition_record` — validate and freeze the exact combined source,
   selection, reference, ledger, and watch record before `make_shorts`.
6. `materialization` — call `make_shorts` once, then persist its exact outcome
   queue separately.

For every phase, first grant the coordinator the global slot:

```bash
python3 '<skill-root>/scripts/run_registry.py' grant-run-lease \
  --run-id '<run-id>' --lease-id '<unique-phase-lease-id>' \
  --phase 'acquisition|selection|reference|acquisition_record|materialization' \
  --op-id '<unique-op-id>'
```

Before every Valmera read, upload, index, watch, render, audit, or mutation in
that phase, atomically reserve one call using the exact returned generation:

```bash
python3 '<skill-root>/scripts/run_registry.py' begin-run-call \
  --run-id '<run-id>' --lease-id '<lease-id>' \
  --lease-generation '<generation>' --phase '<phase>' \
  --thread-id '<parent-thread-id>' --host-id '<parent-host-id>' \
  --call-id '<never-reused-call-id>' --op-id '<begin-op-id>'
```

Make exactly one Valmera call, then end its permit and record the authoritative
current remote job set returned or observed after that call:

```bash
python3 '<skill-root>/scripts/run_registry.py' end-run-call \
  --run-id '<run-id>' --lease-id '<lease-id>' \
  --lease-generation '<generation>' --phase '<phase>' \
  --thread-id '<parent-thread-id>' --host-id '<parent-host-id>' \
  --call-id '<same-call-id>' --outstanding-job-ids-json '[]' \
  --op-id '<end-op-id>'
```

If the client loses the response, keep the permit in flight until the original
HTTP request is known to have stopped. Then end it with every known job ID or a
durable `unresolved:<call-id>` marker instead of guessing `[]`. Keep the same
lease live and use a new call permit only for authoritative state
reconciliation. Replace the marker with observed job IDs or `[]` through that
reconciliation call; a lease carrying an unresolved marker cannot close.

At a safe phase checkpoint, persist exactly one snapshot for that lease:

```bash
python3 '<skill-root>/scripts/run_registry.py' record-run-snapshot \
  <the exact check-run-lease identity arguments> \
  --checkpoint-status 'ready|blocked|paused_safe' \
  --data-json '<phase artifact or structured reason>' \
  --op-id '<run-id>:<phase>:<generation>:snapshot'
```

A `ready` acquisition snapshot is the exact source-acquisition checkpoint:
`parent_project_id`, `source_youtube_video_id`, `source_asset_id`, and
`source_sha256`, with no extra fields. A `ready` selection snapshot must be the exact
normative selection artifact, including its canonical fingerprint. A `ready`
reference snapshot must be the exact normative reference profile. A ready
`acquisition_record` snapshot must be the exact normative acquisition record.
A non-ready snapshot must contain a nonempty `reason`; `blocked` also requires
a nonempty exact `evidence` array. Preserve any already-known phase identity in
that snapshot.

Close only after the snapshot exists, the authoritative outstanding remote job
list is known, and it is empty:

```bash
python3 '<skill-root>/scripts/run_registry.py' close-run-lease \
  --run-id '<run-id>' --lease-id '<lease-id>' \
  --lease-generation '<generation>' --phase '<phase>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --action 'release|freeze|revoke' \
  --checkpoint-status 'ready|blocked|paused_safe' \
  --outstanding-job-ids-json '[]' --reason '<verified evidence>' \
  --op-id '<run-id>:<phase>:<generation>:close'
```

If the job list is nonempty, close is refused and the observed IDs are stored
on the still-live lease for crash recovery. Do not guess `[]`. A `blocked`
coordinator checkpoint freezes that phase and disables new leases, but does not
terminalize the run by itself. Persist the exact coordinator run result, then
finalize it as blocked. `paused_safe` remains resumable. A ready frozen phase
cannot be reopened with different evidence.

## Frozen selection, binding, and materialization

The registry validates the selection schema and canonical fingerprint before
freezing it. It also requires the selection parent to match the frozen source
acquisition checkpoint. Then bind the run:

```bash
python3 '<skill-root>/scripts/run_registry.py' bind-run \
  --run-id '<run-id>' --parent-project-id '<parent-project-id>' \
  --selection-fingerprint 'sha256:<64-lowercase-hex>' \
  --op-id '<run-id>:bind'
```

Binding is immutable. The parent cannot also be a registered child, and the
same parent cannot be bound to another run.

An abstained/empty selection cannot enter reference or materialization. It must
still enter `acquisition_record` once to persist the exact schema-valid
abstained acquisition artifact (`selected_clip_count: 0`, `reference: null`).
Then persist the exact abstained coordinator run result and finalize abstained.

For a nonempty selection, complete the reference phase, then build and freeze
the exact normative acquisition record under an `acquisition_record` lease.
The registry cross-checks it against the run, frozen selection source
identity/watch evidence and selected count, and frozen reference identity. A
materialization lease is refused until that artifact is durably ready.

Call `make_shorts` only under the subsequent live materialization lease. The
ready materialization snapshot stores only the separate outcome queue:

```json
{
  "selection_fingerprint": "sha256:<frozen-selection>",
  "stories": [
    {
      "card": 1,
      "title": "Exact frozen title",
      "approved_start_s": 10.0,
      "approved_end_s": 30.0,
      "status": "materialized",
      "child_project_id": 123,
      "seeded_child_start_s": 10.0,
      "seeded_child_end_s": 30.0,
      "seed_snap_reason": "none",
      "seed_range_verified_by": "authoritative_child_edl",
      "seed_range_evidence_digest": "sha256:<evidence>",
      "generation_job_id": "job-123",
      "generation_failure": null
    },
    {
      "card": 2,
      "title": "Exact frozen title",
      "approved_start_s": 40.0,
      "approved_end_s": 60.0,
      "status": "failed",
      "child_project_id": null,
      "seeded_child_start_s": null,
      "seeded_child_end_s": null,
      "seed_snap_reason": null,
      "seed_range_verified_by": null,
      "seed_range_evidence_digest": null,
      "generation_job_id": "job-124",
      "generation_failure": "Exact observed failure"
    }
  ]
}
```

Stories must cover every selected card and approved range in FIFO order;
`pending` is forbidden in a ready snapshot. A materialized row records the
authoritative child seed range and evidence digest. If it differs from the
approved range, `seed_snap_reason` must be `word_boundary_snap` and
`seed_range_verified_by` must be `audit_snap_keep_to_words`; the coordinator
must have verified the exact two-decimal output of the deterministic word-snap
routine. The registry accepts no unexplained numeric tolerance. The exact
assignment must echo this frozen seed lineage. A failed materialization is
durable and prevents a ready run.

If the process crashes after `make_shorts` but before recording its outcome,
the already-frozen acquisition record survives and the materialization lease
remains live. On restart, reconcile the remote call and its outstanding job IDs
under that exact lease; never call `make_shorts` again merely because the local
outcome snapshot is missing.

## Visible child registration

After materialization, create one visible Codex task per materialized child,
just in time. Task creation is an external one-shot operation: `create_thread`
has no caller-supplied idempotency key. Therefore write the exact assignment and
task intent before making that call.

First prepare the next FIFO intent. `client_request_id` is a never-reused local
correlation ID, not a Codex task ID:

```bash
python3 '<skill-root>/scripts/run_registry.py' prepare-child-task-intent \
  --client-request-id '<never-reused-client-request-id>' \
  --run-id '<run-id>' --selection-fingerprint 'sha256:<selection>' \
  --child-project-id '<child-id>' --card '<card>' --title '<exact-title>' \
  --assignment-id '<assignment-id>' \
  --assignment-fingerprint 'sha256:<assignment>' \
  --assignment-json '<exact-validated-child-assignment>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --op-id '<run-id>:child:<child-id>:task-intent'
```

The registry validates the assignment with repeated upstreams: the exact frozen
selection, acquisition record, and reference profile. CLI identities, approved
range, authoritative seeded range/evidence, source asset and
`source_duration_s`, and reference identity must all agree. It stores the exact
JSON/digest and returns a deterministic `task_marker`.

Immediately before the sole `create_thread` call, atomically claim it:

```bash
python3 '<skill-root>/scripts/run_registry.py' begin-child-task-create \
  --client-request-id '<client-request-id>' --run-id '<run-id>' \
  --child-project-id '<child-id>' --card '<card>' \
  --assignment-id '<assignment-id>' \
  --assignment-fingerprint 'sha256:<assignment>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --op-id '<run-id>:child:<child-id>:task-create-claim'
```

Only a successful `task_create_claimed` response authorizes exactly one
`create_thread` call. Put the returned `task_marker` at the start of the task
title and dormant guard prompt. That prompt must forbid Valmera calls and
mutation-capable work until registration and a live lease packet arrive. A
fresh claim operation after this transition is refused, even if the original
RPC response was lost.

If creation returns only queued `clientThreadId`, persist it immediately:

```bash
python3 '<skill-root>/scripts/run_registry.py' record-child-task-queued \
  --client-request-id '<client-request-id>' --run-id '<run-id>' \
  --child-project-id '<child-id>' --card '<card>' \
  --assignment-id '<assignment-id>' \
  --assignment-fingerprint 'sha256:<assignment>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --client-thread-id '<queued-clientThreadId>' \
  --op-id '<run-id>:child:<child-id>:task-queued'
```

Never pass `clientThreadId` to tools that require a real task ID. Wait/list
until it resolves. Then persist the real `threadId` and `hostId`. Supply
`--client-thread-id` only for the queued path; omit it when `create_thread`
returned real IDs immediately:

```bash
python3 '<skill-root>/scripts/run_registry.py' resolve-child-task-intent \
  --client-request-id '<client-request-id>' --run-id '<run-id>' \
  --child-project-id '<child-id>' --card '<card>' \
  --assignment-id '<assignment-id>' \
  --assignment-fingerprint 'sha256:<assignment>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  [--client-thread-id '<queued-clientThreadId>'] \
  --thread-id '<real-thread-id>' --host-id '<real-host-id>' \
  --op-id '<run-id>:child:<child-id>:task-resolved'
```

Finally, atomically insert the child mapping and consume the resolved intent:

```bash
python3 '<skill-root>/scripts/run_registry.py' register-child \
  --client-request-id '<client-request-id>' \
  --run-id '<run-id>' --selection-fingerprint 'sha256:<selection>' \
  --child-project-id '<child-id>' --card '<card>' --title '<exact-title>' \
  --thread-id '<visible-child-thread-id>' --host-id '<visible-host-id>' \
  --assignment-id '<assignment-id>' \
  --assignment-fingerprint 'sha256:<assignment>' \
  --assignment-json '<exact-validated-child-assignment>' \
  --op-id '<run-id>:child:<child-id>:register'
```

Registration revalidates the exact assignment and requires every CLI/task field
to match the resolved intent before one transaction inserts the child and marks
the intent `consumed`. One child maps to one visible task, and one visible task
maps to one child. A different client request, child/card, assignment, queued ID,
or real task identity is a replacement conflict.

Coordinator and child visible-task roles are reciprocally disjoint across the
registry. `create-run` refuses a coordinator `(hostId, threadId)` already held by
a registered child or resolved child-task intent. Resolution and registration
refuse any child identity already used by a run coordinator, including the same
run's parent task. These checks run in the same SQLite write transaction, so a
concurrent create-versus-resolve race has exactly one winner. If `resume` or
`audit` reports `registry_task_role_collision` / `task_role_collisions`, all
mutations and lease grants fail closed; never reinterpret the coordinator as an
editor or create a replacement task.

Crash recovery is state-specific:

- `prepared`: it is safe to run `begin-child-task-create` once;
- `dispatching`: the external call may already have happened; list/read tasks
  and use bounded task waits to reconcile the exact marker, but never call
  `create_thread` again—even when queued setup is not immediately visible;
- `queued`: preserve the stored `clientThreadId` and wait for its real IDs;
- `resolved`: run `register-child` with the exact frozen data;
- `consumed`: use the registered child; create nothing.

If a `dispatching` marker cannot be authoritatively reconciled, pause fail
closed for user recovery. There is deliberately no cancel, reset, delete, or
replacement command. Titles and summaries alone are untrusted; inspect the
marker in the coordinator-authored guard prompt. Never create a replacement
task for repair.

For `requires_pre_mutation_recast`, no edit lease is legal yet. First persist
the exact lease-free `awaiting_parent_approval` assessment:

```bash
python3 '<skill-root>/scripts/run_registry.py' record-recast-input \
  --run-id '<run-id>' --child-project-id '<child-id>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --recast-json '<exact-awaiting-parent-approval-artifact>' \
  --op-id '<run-id>:child:<child-id>:recast-input'
```

Then persist the coordinator's exact finalized `approved` or `blocked` recast:

```bash
python3 '<skill-root>/scripts/run_registry.py' record-recast \
  --run-id '<run-id>' --child-project-id '<child-id>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --approval 'approved|blocked' \
  --approved-candidate-id '<candidate-id-if-approved>' \
  --approval-reason '<exact decision evidence>' \
  --recast-json '<exact-finalized-recast-artifact>' \
  --op-id '<run-id>:child:<child-id>:recast-decision'
```

The assessment bytes/fingerprint are immutable; only the explicit decision
fields may change. An approved edit validates against both stored artifacts.
A `blocked_before_mutation` assignment instead uses
`block-child-before-mutation` with its exact stored reason:

```bash
python3 '<skill-root>/scripts/run_registry.py' block-child-before-mutation \
  --run-id '<run-id>' --child-project-id '<child-id>' \
  --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --reason '<exact assignment blocked_reason>' \
  --op-id '<run-id>:child:<child-id>:blocked-before-mutation'
```

That transition
marks child/QC blocked without granting a Valmera lease or fabricating editor/QC
results, and FIFO may continue.

## Child edit and parent-QC protocol

Grant the next FIFO child an edit lease:

```bash
python3 '<skill-root>/scripts/run_registry.py' grant-lease \
  --run-id '<run-id>' --child-project-id '<child-id>' \
  --lease-id '<unique-lease-id>' --purpose edit --repair-round 0 \
  --selection-fingerprint 'sha256:<selection>' \
  --assignment-id '<assignment-id>' \
  --assignment-fingerprint 'sha256:<assignment>' \
  --op-id '<unique-grant-op-id>'
```

The registered child must run `begin-call` immediately before every Valmera
call, using the run/child/lease/generation/purpose/round/selection/assignment
identities plus its registered `thread_id`, `host_id`, a never-reused `call_id`,
and an `op_id`. Make exactly one call, then run `end-call` with that same exact
identity and the authoritative current outstanding job IDs. Any mismatch, old
generation, non-live state, in-flight competing call, or missing database is a
hard stop. `check-lease` is diagnostic only.

At the end of the attempt, record either:

- the exact valid normative editor result for `ready`, `needs_repair`, or
  `blocked`; or
- a `valmera-safe-checkpoint-v1` object for `paused_safe`.

```bash
python3 '<skill-root>/scripts/run_registry.py' record-child-result \
  <all exact check-lease identity arguments> \
  --status 'ready|needs_repair|blocked|paused_safe' \
  --result-json '<complete-result-object>' \
  --exclusions-json '<complete-current-exclusions-object>' \
  --op-id '<run-id>:child:<child-id>:round:<n>:result'
```

For a complete attempt, the registry validates the result against the exact
stored assignment and, when applicable, the exact approved recast. It separately
requires the runtime lease ID, fenced generation, purpose, and repair round to
equal the active lease. Repair therefore reuses the immutable assignment but
reports its new lease/generation. The result must echo the exact source/seed
lineage, including source duration.

Close the child lease only after the safe checkpoint and empty outstanding job
list are independently verified. The close command uses coordinator identity,
not child identity.

Every completed editor result—including `needs_repair` and `blocked`—must be
followed by independent parent QC. The editor cannot self-authorize repair.
After safely closing the editor lease, grant `purpose=qc` for the same child and
round. The holder is automatically the registered coordinator. It obtains an
atomic `begin-call`/`end-call` permit around every full-render read/watch/audit,
then records the exact valid parent-QC artifact:

```bash
python3 '<skill-root>/scripts/run_registry.py' record-qc-result \
  <all exact QC check-lease identity arguments> \
  --status 'pass|repair_required|blocked' \
  --result-json '<complete-parent-qc-object>' \
  --exclusions-json '<complete-current-exclusions-object>' \
  --op-id '<run-id>:child:<child-id>:qc:<round>:result'
```

The registry validates parent QC against the exact stored editor result for the
same repair round, not merely against the standalone schema. Parent source
lineage, editor claims, run/card/task identity, versions, and evidence must agree.

Close QC `pass` with checkpoint `ready`, `repair_required` with `paused_safe`,
and `blocked` with `blocked`. FIFO does not advance until parent QC is terminal
(`pass` or `blocked`) and its lease is closed.

## Repairs, pausing, and restart recovery

Only a recorded parent-QC `repair_required` authorizes `purpose=repair`. Grant
the repair to the same visible child task with the next round and a new lease
ID/generation. Send one consolidated repair packet. Record a fresh full editor
result, then perform a fresh independent parent full-render QC.

There are at most two repair rounds. At round 2, another `repair_required`
result is refused; parent QC must record a truthful terminal `blocked` result.

`freeze`, `release`, and `revoke` all terminate only the exact lease token.
They do not delete the child mapping. A future valid transition may receive a
new token/generation; the old token remains permanently unauthorized. None is
a force action: all require a compatible recorded checkpoint and no outstanding
jobs.

On every coordinator restart, before creating tasks or calling Valmera, run:

```bash
python3 '<skill-root>/scripts/run_registry.py' resume --run-id '<run-id>'
python3 '<skill-root>/scripts/run_registry.py' audit
```

`resume` is a single consistent database snapshot containing the run, validated
phase snapshots/digests, child mappings, all attempt history, exclusions,
call permits, frozen leases, and the global live lease. If it reports a live
lease or an in-flight call requiring reconciliation, contact that exact mapped
holder and reconcile Valmera state. Do not grant another lease. Never revive a
closed lease or reuse a call ID.

## Finalization

With no global live lease and every prepared task intent consumed (or no intent
needed for a failed/unmaterialized arc), first persist the exact terminal
normative `coordinator-run-result`. An open `prepared`, `dispatching`, `queued`,
or `resolved` intent blocks terminal result persistence:

```bash
python3 '<skill-root>/scripts/run_registry.py' record-run-result \
  --run-id '<run-id>' --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --result-json '<exact-coordinator-run-result>' \
  --op-id '<run-id>:coordinator-result'
```

This artifact is immutable. The registry validates it against every available
frozen selection/acquisition/reference artifact, binds run/topic/parent/source/
reference identities, derives every count and arc generation status from the
materialization queue, and derives editor/QC status and EDL versions from the
latest trusted attempts. `blocked_before_selection` is the only branch that may
validate without a selection; it must match an exact blocked acquisition or
selection checkpoint. A reference-stage block may truthfully keep unavailable
reference identities null. Once recorded, no new coordinator, child, repair, or
QC lease may be granted.

Then finalize using coordinator identity:

```bash
python3 '<skill-root>/scripts/run_registry.py' finalize-run \
  --run-id '<run-id>' --coordinator-thread-id '<parent-thread-id>' \
  --coordinator-host-id '<parent-host-id>' \
  --status 'ready|blocked|abstained|paused_safe' \
  --op-id '<run-id>:finalize:<status>'
```

- coordinator `ready_for_studio_export` maps only to registry `ready` and
  requires exact frozen selection/materialization coverage, no failed
  materialization, every registered materialized child, and parent QC `pass`
  for every child.
- coordinator `partial` or `blocked` maps only to registry `blocked`. After a
  ready materialization outcome, no terminal result is legal while a generation
  is pending or any generated child lacks terminal parent QC.
- `blocked_phase: materialization` is legal for the derived queue only when all
  selected arcs failed generation: `generated_count == 0` and
  `failed_generation_count == selected_arc_count`.
- When `ready_count == 0`, at least one child was generated, and every generated
  child is terminally QC-blocked, use `blocked_phase: child_qc`. Failed-generation
  siblings may coexist and remain explicitly accounted; do not relabel this as
  a materialization block.
- `partial` requires at least one ready/pass child and at least one failed-
  generation or terminally blocked arc, with zero pending generations and no
  nonterminal generated child.
- coordinator `blocked_before_selection` also maps only to registry `blocked`.
- coordinator `abstained` maps only to registry `abstained` and requires exact
  empty selection plus the frozen abstained acquisition record and no children.
- `paused_safe` requires no live lease and remains resumable.

`ready`, `blocked`, and `abstained` are immutable terminal states.

## Idempotency and failure handling

- Every mutation requires an `op_id`. The registry stores the request hash,
  response JSON, and exit code atomically.
- Retrying the identical command and payload under the same ID replays the
  exact stored result. Reusing the ID with changed input fails.
- Failed preconditions are also replayed. If external/durable state later
  changes, use a new attempt `op_id`; do not reuse the failed one.
- A changed outstanding-job observation is a changed request and therefore
  needs a new close-attempt `op_id`.
- Never reuse a lease ID. Never release by timeout. Silence is not evidence that
  Valmera stopped.
- Preserve complete current exclusions on every child/QC result, not a delta.
- Never manually edit the SQLite database; keep its event and operation trail.

## Checkpoint compatibility

| Holder/phase | Recorded result | Close checkpoint |
|---|---|---|
| coordinator phase | ready phase snapshot | `ready` |
| coordinator phase | structured safe pause | `paused_safe` |
| coordinator phase | structured terminal failure | `blocked` |
| edit or repair | child `ready` | `ready` |
| edit or repair | child `blocked` | `blocked` |
| edit or repair | child `needs_repair` or `paused_safe` | `paused_safe` |
| parent QC | QC `pass` | `ready` |
| parent QC | QC `repair_required` | `paused_safe` |
| parent QC | QC `blocked` | `blocked` |

Every close additionally requires `outstanding_job_ids=[]`. Run, holder,
child, phase, purpose, repair round, selection, assignment, lease ID, and lease
generation mismatches are refused atomically.
