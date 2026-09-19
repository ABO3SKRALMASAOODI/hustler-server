# Orchestration v7

## Why this control plane exists

The production path has three worker MCP dispatcher lanes by default and three
media dispatcher lanes. Remote executor ceilings are higher. The useful
workflow limit is therefore three concurrent Valmera requests, not one global
request and not an unbounded number of logical leases.

One child project is still serial: never issue a second request for a child
while its prior request or returned job is nonterminal. Different children may
run in parallel.

## Finite state machine

The run stages are:

```text
created -> taste -> source -> selection -> materialization
        -> editing -> qc -> exporting -> complete
```

The child states are:

```text
queued -> editing -> candidate -> ready -> exporting -> exported
                    |          |
                    v          v
                  repair <------

queued/editing/candidate/repair -> needs_user_review | failed_technical
```

All changes go through `scripts/run_state.py`, which uses an advisory file lock
and atomic replacement. It records outcomes, not permission tokens. Valmera job
IDs are durable reconciliation handles.

## Roles

### Coordinator

The coordinator owns:

- source identity and acquisition;
- transcript-wide story selection;
- taste profile and style-lane distribution;
- materialization and child identity mapping;
- editor assignment and the three-request budget;
- independent candidate review and repair packets;
- final export, local verification, manifest creation, and reporting.

The coordinator must remain active in the current turn. It may do local QC
while three editor calls are in flight, but it may not add a fourth Valmera
request. If it needs Valmera, wait for one lane to become free.

### Editor subagent

An editor receives one child at a time and owns its mutations until it returns
a candidate or terminal failure. It does not select stories, alter the parent,
touch siblings, approve itself, export, publish, schedule automations, or spawn
more agents.

Reuse the same three agents with follow-up assignments. This keeps context
warm without creating 8–26 sidebar tasks and avoids task-registration drift.

## Pool algorithm

1. Spawn up to three editors, never more than the number of queued children.
2. Assign one child to each and mark it `editing`.
3. Wait for whichever editor returns first. Use direct agent waiting; do not
   create a timer, cron job, or heartbeat.
4. Validate the editor's local evidence bundle.
5. Review the candidate:
   - pass: mark `ready`, then give that editor the next queued child;
   - fail: mark `repair`, send one consolidated repair packet to that editor;
   - tool failure: reconcile its recorded job ID before retrying anything.
6. Continue until no child is queued, editing, candidate, or repair.
7. Reconcile live EDL versions once, then export ready children with at most
   three final jobs active.

An editor waiting on a Valmera job is active. A stopped, failed, or completed
editor is not active. Never count a database row, lease, or sidebar task as
actual compute capacity.

## Polling and recovery

- Prefer a tool's bounded job wait. If it remains running, record the job ID
  and let other workers progress.
- Poll only a known nonterminal job and back off when state is unchanged.
- Never create background wakeups merely to poll.
- On resume, inspect `run.json`; for every recorded nonterminal job, query it
  once. Update the state from the authoritative result before new mutations.
- If a mutating call's response is lost, compare the current EDL/version and
  job history with the recorded pre-call version. Do not blindly repeat an
  additive edit.
- Do not expire or reassign an editor claim automatically. The coordinator
  explicitly recovers it after determining that the agent stopped.

## Resource discipline

QA output must be bounded. Keep the current candidate, current evidence bundle,
and at most one failed predecessor per child. A full run should not create
thousands of frame files. Contact sheets are preferred over individual stills;
targeted stills are created only around suspected defects.

Default flow-control caps:

- 3 editor subagents;
- 3 concurrent Valmera requests/jobs;
- 2 editor repair rounds per child;
- 1 coordinator rescue pass;
- 2 retained preview generations per child after acceptance.

There is deliberately no cap, target, minimum, or default for selected stories.
All and only distinct candidates that clear the story-quality bar enter the
queue; the queue is processed in waves of at most three. These are flow-control
limits, not quality shortcuts. A short that cannot pass within them becomes a
visible exception instead of an infinite loop.
