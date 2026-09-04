# Valmera reliability baseline — 2026-09-04

This is the operational comparison point for subscriber projects, MCP editing,
and remote execution after the full-reliability release. It contains no user
content or credentials. Recompute every metric from production before claiming
an improvement; do not treat silence or a missing row as success.

## Pre-release production evidence

The audit immediately before this release found:

- Subscriber jobs over the inspected seven-day window: 214 done and 51 failed
  (19.2% failed). The largest concrete clusters were preview duration mismatch
  (14), an `agent_loop` metric-closure crash (15), Cloudflare capacity-busy
  launches (10), empty-sequence `max()` crashes (6), and unrecovered
  Cloudflare calls (6).
- MCP jobs: 4,084 done and 260 failed. The initial broad audit classified 192
  nominally-done calls as refusals; the reproducible strict metric finds 186
  results beginning with `REJECTED:`. Use the strict metric for future trend
  comparisons rather than silently changing the denominator. Cloudflare
  capacity accounted for 244 failed calls and Modal billing/capacity for 13.
- Stock-media continuity was broken across process boundaries: 129 chosen IDs
  were unknown when used later, and only 3 of 140 observed `add_stock_media`
  calls completed successfully.
- The audited Shorts batch accepted 3 of 26 candidates. Rejections clustered
  around framing, caption, B-roll, and quality-control evidence.
- One subscriber project reached EDL version 27 through repeated continuation
  slices, remained `repair_required`, and never received a terminal reply.
- Four of 20 failed subscriber logical requests had no assistant reply before
  the subscriber's next request; all four ended in the old Cloudflare-busy
  path. One of those subscribers sent no later request and was left waiting.
  Future snapshots must report this reply coverage rather than relying on the
  queue state alone.
- The provider ledger briefly exposed one expired Modal preview as `running`
  for more than four days even though its canonical queue job was already
  `failed`. The live reaper closed it during the read-only audit without manual
  mutation, confirming the existing crash-reconciliation path works; this
  release preserves the invariant and its regression coverage.

These are historical measurements, not permanent thresholds. The watcher must
use a comparable time window and distinguish newly-created work from old jobs
whose terminal rows remain in the database.

## Release invariants

### Agent turns and subscriber projects

- A logical user request has a durable, bounded productive-slice budget.
- Every terminal logical request receives a user-visible final response,
  including bounded explanations for unrecoverable infrastructure failures.
- Progress is compared with the original durable frontier; a continuation may
  not reset its own budget by writing another EDL version.
- Explicit thumbs-up/down feedback is counted. No rating is reported as
  `no signal`, never inferred to be satisfaction.
- The admin subscriber ledger retains every ever-paid customer, including
  canceled and past-due accounts, while showing current entitlement separately.

### MCP

- Tool refusals and failures surface as MCP errors with a structured
  `tool_outcome`; a nominally successful transport may not hide a rejected edit.
- Search-result handles are project-scoped, durable across processes, merged
  transactionally, and bounded.
- MCP calls for one project route consistently to one Cloudflare shard while
  retaining unique call identities.

### Remote execution

- A proven Cloudflare capacity-busy response defers work without spending a
  retry attempt or pretending that a provider accepted the call.
- Busy deferrals are bounded and delayed to prevent hot-loop claiming.
- Remote ownership and executor leases are fenced durably. An expired executor
  is stopped and recorded as a terminal transient-infrastructure failure.
- Unknown or ambiguous provider state never authorizes duplicate execution.
- Cloudflare remains the production executor. Modal remains an explicitly
  invoked disaster-recovery path.

### Rendering and uploads

- Insert-only proof windows clip and rebase inserted media correctly, including
  windows containing no A-roll.
- A short repaint patch cannot truncate the main output when its own input ends.
- Empty range collections never reach `max()` without a default or guard.
- Failed obsolete preview/final retries are rejected once a newer render exists.
- Original videos and clip attachments share the advertised 14 GB ceiling.
- Multipart uploads use bounded 8–16 MiB parts, remain below 1,000 presigned
  URLs at the product maximum, and use presigns long enough for slow uploads.
- Empty files are rejected before presigning. High-resolution reference images
  up to 50 MiB are accepted.
- Large media trays remain a selection pool instead of being blindly appended
  to an authored edit.

### Billing

- Payment truth comes from the newest timestamped payment attempt, independent
  of provider response order.
- A captured retry clears an older decline.
- Transaction upserts backfill subscription identity and origin when Paddle
  supplies them after an initial failure.

## Verification completed before release

- Worker pytest suite: 1,730 passed, 3 skipped.
- Legacy worker executable checks: all 11 harnesses passed, including 1,038
  unit checks, 22 patch checks, and 30 text-behind-subject tests.
- Backend pytest suite: 326 passed, including the snapshot classification tests.
- Modal executor tests: 38 passed.
- Cloudflare adapter TypeScript check: passed.
- Frontend library suite on the current production base: 63 passed.
- Next.js production build: 266 static/dynamic routes generated successfully.
- Plugin-v7 tests in the preserved user worktree: 65 passed. Those uncommitted
  plugin files are not part of this release.
- Release diff audit: no binary files, credential-pattern matches, whitespace
  errors, or non-owner commit identities.

## Deployment and observation

- Generate comparable aggregate telemetry with
  `python backend/scripts/reliability_snapshot.py --days 7` in an environment
  that supplies `DATABASE_URL`. The command forces a read-only database session
  and emits no user, project, message, raw-error, or credential values.
- Backend pushes are not considered live until `/healthz` reports `status=ok`,
  `role=backend`, and the exact pushed commit prefix.
- Frontend pushes are not considered live until `/api/health` reports
  `status=ok`, `role=frontend`, and the exact Vercel Git commit SHA with
  `Cache-Control: no-store`.
- Cloudflare's staged rollout must pass its source-fingerprint verifier for all
  lanes. Do not deploy Modal as part of an ordinary worker release.
- No production database migration is required by this release.
- During the first 24 hours, compare newly-created subscriber and MCP work with
  this baseline. Notify on a new root-cause cluster, a stalled logical request,
  hidden MCP refusal, executor ownership contradiction, material failure-rate
  increase, billing inconsistency, or explicit negative feedback. Stay quiet
  when state is unchanged and non-actionable.
