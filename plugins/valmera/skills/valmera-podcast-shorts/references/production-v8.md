# Coordinator production and review

## Start and resume

Use `run_state.py --help` for the durable command interface. State format v7 is
retained; this does not require loading every v7 creative contract.

Initialize with source identity and a run-local `delivery-quality-v1` policy.
Use final minimum and target `[1080,1920]`, genuine source dimensions and crop
budget. Preserve an explicit source-only quality exception when the user has
accepted archival footage; it cannot waive low-resolution captions or branding.
`delivery-quality-v7.md` documents the existing quality evidence data shape if
needed. Reuse the source acquisition record, hash and measured dimensions across
children; do not manufacture one new evidence pipeline for each.

Advance phases once: taste, source, selection, materialization, editing, qc,
exporting. `status --json` is the resume entry point. Reconcile recorded jobs
and live EDLs before resuming mutations; completed historical exports remain
immutable. Never expire an active editor just because a lease is old.

## Freeze a useful assignment

Each assignment contains run/short/parent/child IDs, selected source range and
verbatim transcript, viewer question/hook/turn/payoff, chosen lane and why,
concrete visual idea, reference evidence paths, output geometry, caption
intent, needed assets, duration, intentional silence and known risks.
Preserve every necessary qualifier and enough source handles for natural cuts.
Keep it concise enough that the editor can see the story rather than a checklist.

Materialize explicit ranges once and `add-short` each returned ID. The shared
parent index is authoritative. If a desired visual effect seems to require
re-uploading the dialogue, inspect the native tools first; v8 supports inset
picture geometry, independent headlines and picture-only B-roll covers.

## Keep the three editors productive

Use three reusable `gpt-6.1-sol` subagents when at least three children exist.
Each receives creative-v8, editing-v8 and one assignment. An editor owns only
one child until it returns. At most three Valmera jobs are in flight; a running
job still uses a slot while nobody is polling it. Refill a free editor with an
independent queued child before optional deep review. A render or export delay
on the pilot must not stop the other children.

The state machine's candidate command releases ownership. Record returned
candidates in arrival order, refill available slots, then record independent
QC. Claims are bookkeeping, not extra compute slots. Wait on agents directly;
do not create automations or separate user-owned threads. Search/cache useful
B-roll once when several assignments need the same subject. Use a shared
registry with attribution/rights and non-overlapping source ranges.

## Independent review

Review the actual MP4 and relevant reference evidence, not only the candidate
summary. Use the existing packet; do not regenerate identical sheets or ASR.
Inspect actual moving transitions and speech with a supported video/audio
review tool. If that tool cannot assess the media, record the limitation and
use another available evidence path; never accept a capability-denial response.

**First review story and design.** Write the four observations from creative-v8
(hook/payoff, strongest designed moment/reference, weakest moment, substance
without celebrity/title). Reject a generic slide treatment, meaningless
cutaways, tiny captions or an unjustifiably flat whole batch even if the media
probe passes. A deliberate quiet emotional passage can be excellent without
decorative effects. Review the whole batch's variety by content, without quotas.

**Then review execution.** Check identity/current EDL, unclipped speech and
preserved meaning, caption accuracy/readability/sync, meaningful cues,
face/headline/brand separation, B-roll identity/relevance/rights/uniqueness,
intentional silence, and a complete payoff. Use actual playback for timing;
still frames and valid metadata cannot establish it. Detector flags are leads:
intentional bars/cards/stills/silence are not corruption.

Follow lane-specific structure from SKILL.md. The headline stays through its
editorial program; other lanes needn't inherit that constraint. Montage must
stop dialogue completely, include a recognizable featured-person anchor and
visibly pay off its premise; an all-product/interface sequence fails this
owner's montage brief even if it illustrates the words. Action openings
show the actual recognizable subject doing something. No repeated B-roll
moment within one short or incidental metadata subheadline.

Write one consolidated timestamped repair packet if needed. Two editor repair
rounds, then one bounded rescue or a visible exception. Scope additional
frame/audio measurements to the identified defect. Do not spend hours proving
the same unaffected pixels still work. Final-review identity and full decode
are still required after a change.

The existing state command needs a score >=90 and explicit QC fields; derive
that score from the actual observations. It is a compatibility record, not
proof of taste. `caption_quality_check`, requested active-word check,
within-short uniqueness, montage checks when applicable, and the checksum-bound
delivery review must reflect actual evidence. Do not loosen state validation
to pass a bad candidate.

## Export and delivery

Confirm the live EDL once before acceptance/export, with no active mutation.
Export only the reviewed version using Valmera's final/export route; don't
encode a local substitute as a native export. At most three final jobs at once.
Verify one actual final early while other work continues: HD canvas, correct
duration, legible corner mark and complete 5s ending. Final-only branding must
be inspected in the final. Don't change account-wide watermark settings per
child while concurrent jobs are rendering; use the current approved setting.

Every final begins `<style-lane>__<short-id>__...mp4`. Probe/decode/hash it once,
confirm exact job/EDL, actual streams and branding, and record it with
`run_state.py export`. Use `inspect_cut.py --program-duration <editorial-seconds>`
to include final brand-card samples. Retain the accepted candidate and final;
no need to preserve hundreds of redundant frame dumps. Never delete original
sources or prior run deliveries during cleanup.

Finish with `status --json` and `finalize`. The manifest must account for every
selected ID as exported, needs_user_review or failed_technical. Keep downstream
CRM/publishing outside this skill. Provide local files and the manifest, the
best visible creative choices, exceptions, elapsed time, number of previews and
re-index operations. A clean small sample is evidence of improvement; only a
future independent full run demonstrates batch-wide autonomous performance.
