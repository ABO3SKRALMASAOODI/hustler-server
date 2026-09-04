# Valmera reliability baseline — 2026-09-04

This is the operational comparison point for subscriber projects, MCP editing,
and remote execution after the full-reliability release. It contains no user
content or credentials. Recompute every metric from production before claiming
an improvement; do not treat silence or a missing row as success.

## Pre-release production evidence

The audit immediately before this release found:

- Subscriber jobs over the inspected seven-day window: 216 done and 51 failed
  (19.1% failed). The largest concrete clusters were preview duration mismatch
  (14), an `agent_loop` metric-closure crash (15), Cloudflare capacity-busy
  launches (10), empty-sequence `max()` crashes (6), and unrecovered
  Cloudflare calls (6).
- MCP jobs: 4,084 done and 260 failed. The initial broad audit classified 192
  nominally-done calls as refusals; the reproducible strict metric finds 186
  results beginning with `REJECTED:`. Use the strict metric for future trend
  comparisons rather than silently changing the denominator. The complete
  outcome monitor also reports correction/recipe, prerequisite, transient,
  unavailable, unsafe, and structured-error results separately; do not treat
  a transport-level `done` row as a successful tool action. On the same final
  snapshot, the 4,084 `done` rows decomposed into 3,798 actual successes, 218
  correction/refusal outcomes, 64 transient-failure outcomes, and 4 remaining
  structured errors: 286 agent-visible non-successes in total. Cloudflare
  capacity accounted for 244 failed calls and Modal billing/capacity for 13.
  Ten completed rows carried `is_error=true`; that overlapping flag count is
  reported as `mcp_done_is_error_flagged`, not another structured-error bucket.
- Stock-media continuity was broken across process boundaries: 129 chosen IDs
  were unknown when used later, and only 3 of 140 observed `add_stock_media`
  calls completed successfully.
- Of the newly-visible MCP non-successes, 64 were transient results: 35 URL
  fetches, 14 preview renders, six stock searches, five media deliveries,
  three frame inspections, and one asset-frame inspection. Twenty-eight of
  the URL fetches named a Cloudflare anti-bot
  challenge and yt-dlp's missing browser-impersonation dependency; the worker
  image now installs the dependency version pinned by the deployed yt-dlp
  release. Nineteen calls also wasted a round on the internal `load_tools`
  pager even though MCP already receives the complete catalog and each MCP
  tool call has an isolated worker context; that pager is no longer exposed
  over MCP.
- The 14 nominally transient preview results included deterministic empty-
  canvas EDL failures and exhausted Modal account capacity. Changed-section
  proof failures now preserve the executor's structured decision: repairable
  EDL defects request a new version, genuinely transient infrastructure may
  retry, and non-retryable provider capacity is reported unavailable without
  inviting a blind render or edit rewrite.
- The audited Shorts batch accepted 3 of 26 candidates. Rejections clustered
  around framing, caption, B-roll, and quality-control evidence.
- One subscriber project reached EDL version 27 through repeated continuation
  slices, remained `repair_required`, and never received a terminal reply.
- Four of 20 failed subscriber logical requests had no assistant reply before
  the subscriber's next request; all four ended in the old Cloudflare-busy
  path. One of those subscribers sent no later request and was left waiting.
  Future snapshots must report this reply coverage rather than relying on the
  queue state alone.
- The exact paid-subscriber ledger contained 14 ever-paid customers: 11 had
  parent projects and three had none. Those 11 owned 23 historical parent
  projects: 21 active, one billing-attention, and one canceled. The current-
  entitlement subscriber-project admin view therefore correctly contained 22
  projects owned by 10 customers; it intentionally excluded the one canceled
  customer's project. Three
  active projects ended on an unanswered user message; two newest EDLs were
  still marked `repair_required`; one untouched seed EDL had no preview yet;
  and two main-video index paths were terminal failures. One of those main
  failures was invisible to the studio's self-heal because a later successful
  attachment index became the project's nominal "latest index". Project
  health must stay asset-scoped: clips and music cannot mask or spend the
  retry budget for the active original.
- The provider ledger still exposed one expired Modal preview as `running`
  for more than four days even though its canonical queue job was already
  `failed`. The audit was intentionally read-only and did not alter it. This
  release adds an idempotent reaper repair that will inherit the exact terminal
  queue state after deployment; future snapshots must show the contradiction
  disappearing rather than silently accepting it as baseline noise.
- The public MCP boundary returned its server card with identity
  `io.valmera/video-editor`, 135 currently deployed tools, and all 12 session
  tools. An unauthenticated JSON-RPC call returned 401 with the required
  `resource_metadata` challenge, and both OAuth metadata documents returned
  coherent HTTPS endpoints. Tool-count changes are valid only when explained
  by the exact deployed release (this candidate intentionally removes the
  internal `load_tools` pager from the public catalog).

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
- Prerequisites, unsafe operations, provider unavailability, and both legacy
  transient-failure spellings are non-successes too. They remain structured,
  are never reported as fulfilled work, and a prerequisite-only in-house turn
  is not billed.
- Backend and session MCP tools now set `isError` on handled validation,
  lookup, upload, artifact, stale-tool, and denial failures. Every public MCP
  error response records a bounded `mcp_error_response` event containing only
  the tool name; the pre-release historical count is unavailable by
  construction.
- Attachment transport is independently verified: an object-store read error
  cannot erase an otherwise usable text/link response, but missing promised
  frames, audio, or an explicitly requested inline video sets `isError` and
  enters the public-response counter. Partial visual-evidence batches are no
  longer reported as complete. The public payload keeps the worker's
  `tool_outcome` (including whether an edit already changed state) and reports
  the later attachment failure separately as `delivery_outcome`, preventing a
  caller from blindly repeating a successful mutation.
- The operator dashboard uses the same complete outcome vocabulary as the
  snapshot. On the final read-only seven-day check it showed 546 non-successes
  across 4,344 MCP jobs: 260 terminal queue failures plus 286 completed calls
  whose agent-visible result was not successful. The 218 correction/unsafe
  refusals remain visible as their own subset, and public `isError` responses
  are shown separately because they can overlap queue-backed calls.
- Generated Shorts children carried 4,230 of those 4,344 MCP jobs. The
  parent-only admin table previously omitted them; its tool totals now roll up
  the parent and every child in one aggregate while keeping child projects out
  of the top-level project list.
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
- The snapshot found one old Modal ledger row still marked running after a
  newer queue lease had already finished. Reconciliation now closes both
  exact terminal leases and superseded terminal leases; the latter are marked
  cancelled without rerunning work or contacting the provider.
- Cloudflare remains the production executor. Modal remains an explicitly
  invoked disaster-recovery path.

### Rendering and uploads

- Insert-only proof windows clip and rebase inserted media correctly, including
  windows containing no A-roll.
- Changed-section proof failures retain their retry policy when presented to
  the editing agent; deterministic EDL defects, transient infrastructure, and
  provider unavailability are distinct outcomes.
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

- Only the first paid/completed state of a specific Paddle transaction refreshes
  the monthly pool. Repeated `subscription.updated`, `transaction.paid`, or
  `transaction.completed` deliveries preserve spent credits. The payment row
  and entitlement update commit together under a transaction-scoped lock, so
  concurrent delivery or a process crash cannot double-refill or lose a
  renewal; a late failure snapshot cannot downgrade confirmed paid/completed
  truth. Unknown prices and completed recurring events without a subscription
  id fail closed for provider retry instead of creating a zero-credit
  subscription.
- The hourly Paddle reconciler uses the same paid-transition claim. If an
  active-subscription webhook lands but its transaction webhook is lost, the
  backfilled payment and credit refresh commit together exactly once; an
  unknown price rolls both back for the next tick instead of losing the grant.
  A failed or malformed transaction-history response is reported as provider
  uncertainty and cannot downgrade access or classify a customer as never-paid.
- An interim `transaction.paid` without `subscription_id` is acknowledged but
  deliberately left unclaimed until Paddle's completed event attaches the
  recurring contract. Completed one-time charges are recorded as revenue
  without creating subscription entitlement, while malformed completed
  recurring charges remain retryable. Linked zero-dollar transactions are
  ledger-only: the signed subscription lifecycle event, not event arrival
  order, establishes trial allowance and daily-credit state.
- The shared request-scoped database connection is explicitly rolled back and
  closed at Flask context teardown. Partial webhook work cannot linger until
  interpreter garbage collection or escape into a reused database session.
- Paddle webhooks fail closed before parsing or account access when the signing
  secret is absent, allowing provider retries without accepting forged plan or
  credit mutations. Raw request bodies are authenticated against every `h1`
  supplied during Paddle secret rotation rather than only the final header
  value. An existing stored subscription is the authoritative identity for
  renewals; a first purchase must resolve the payer's email through Paddle.
  Provider lookup failures return 503 for retry and never fall back to the
  browser-controlled `custom_data.user_id`.
- Payment truth comes from the newest timestamped payment attempt, independent
  of provider response order.
- A captured retry clears an older decline.
- Transaction upserts backfill subscription identity and origin when Paddle
  supplies them after an initial failure.
- Synchronous checkout, plan-change, resume, cancellation, subscription-state,
  and Brevo statistics calls have bounded connect/read deadlines. Provider
  stalls return an explicit retryable response instead of occupying a web
  worker indefinitely; the hosted checkout defaults to the live Creator plan
  and logs only provider status, never Paddle's customer/transaction body.

## Verification completed before release

- Worker pytest suite: 1,735 passed, 3 skipped.
- Legacy worker executable checks: all 20 harnesses passed, including 1,038
  unit checks, 22 patch checks, and 30 text-behind-subject tests.
- Backend pytest suite: 387 passed, 4 skipped, including MCP protocol, billing
  trust-boundary, secure-health, and
  snapshot classification tests.
- Modal executor tests: 38 passed.
- Cloudflare adapter TypeScript check: passed.
- Frontend library suite on the current production base: 66 passed.
- Next.js production build: 266 static/dynamic routes generated successfully.
- Frontend production-runtime smoke: passed for exact health identity and
  no-cache headers, public pages, signed-out protection redirects, and the
  authenticated Studio request boundary.
- Plugin-v7 tests in the preserved user worktree: 65 passed. Those uncommitted
  plugin files are not part of this release.
- Release diff audit: no binary files, credential-pattern matches, whitespace
  errors, or non-owner commit identities.

## Deployment and observation

- A production database credential was present in tracked operator guidance
  before this release. The plaintext value has been removed from the working
  tree, but deletion does not revoke copies in Git history. Rotate that
  database credential and update every legitimate deployment secret before
  release; do not consider repository cleanup alone sufficient containment.
- Before pushing, run
  `python backend/scripts/security_release_preflight.py --env-file <authorized-env>`.
  It prints only setting names and bounded reasons, rejects the one-way
  fingerprint of the exposed database URL, and fails until the application,
  Paddle API, and Paddle webhook secrets are production-safe.
- Backend and Cloudflare release gates run
  `backend/scripts/scan_tracked_secrets.py` before certification or publish.
  It scans every Git-tracked text file, reports only path/line/category, and
  blocks credentialed production database URLs, private keys, and common live
  token shapes while allowing explicit local integration fixtures.
- The frontend release verifier runs its equivalent dependency-free tracked
  secret scan before package installation, tests, build, production-runtime
  smoke, and Vercel revision verification. Frontend agent guidance uses the
  same Cloudflare-primary, manual-fallback deployment model as the backend
  repository.
- Generate comparable aggregate telemetry with
  `python backend/scripts/reliability_snapshot.py --days 7` in an environment
  that supplies `DATABASE_URL`. The command forces a read-only database session
  and emits no user, project, message, raw-error, or credential values.
- Backend pushes are not considered live until `/healthz` reports `status=ok`,
  `role=backend`, and the exact pushed commit prefix. The same commit's release
  status also runs the complete backend test suite before it can turn green.
  This comprehensive workflow is the sole writer of the `render-production`
  commit status; the obsolete one-test lock-flow hotfix gate was removed so it
  cannot race or falsely certify a release.
  Once that exact commit is live, the gate immediately certifies the public MCP
  server card, tool-count integrity, all four OAuth metadata aliases, endpoint
  coherence, PKCE support, and unauthenticated Bearer challenge. The periodic
  watcher remains the later drift detector rather than the first release test.
- Backend health remains `degraded` when the stable application signing key,
  Paddle webhook signing secret, or Paddle API key is absent, or when the
  database URL is missing, malformed, local, or still matches the exposed
  credential. An optional direct scheduler URL is checked the same way, and
  production health also rejects Paddle sandbox mode. A missing
  application key uses an unpredictable process-local fallback rather than the
  old public literal, so misconfiguration is disruptive but never silently
  forgeable. Degraded health returns HTTP 503 so hosting and release checks
  cannot mistake unsafe configuration for a healthy rollout.
- Render startup now starts only the current Gunicorn application. The retired
  app-builder's pre-boot filesystem scan and legacy `jobs` table rewrite were
  removed; video recovery remains in the durable video worker/remote ledger,
  where ownership and terminal-state rules are tested.
- Flask app construction no longer creates or alters tables in each Gunicorn
  worker. The inherited base tables and migrations 013–018 now live with every
  other numbered file in `backend/migrations/`, so the Docker/local runner can
  construct a complete fresh schema before starting the app. Production still
  uses controlled manual SQL: its migration ledger predates most historical
  files, so the convenience runner explicitly must not be replayed there.
  This candidate adds no new production schema requirement; the migration
  changes only consolidate already-applied historical definitions.
- Frontend pushes are not considered live until `/api/health` reports
  `status=ok`, `role=frontend`, and the exact Vercel Git commit SHA with
  `Cache-Control: no-store`.
- Cloudflare's staged rollout must pass its source-fingerprint verifier for all
  lanes. Agent-facing repository guidance names Cloudflare as primary and
  Modal/Cloud Run as manual fallbacks; do not deploy either fallback as part of
  an ordinary worker release. Every Cloudflare, Modal, or Cloud Run executor
  deployment runs the complete worker test suite and capability validator
  before publishing; worker Python must compile and the Cloudflare adapter must
  type-check too. The executor workflows also reject the exposed database
  credential before contacting a provider.
- Python runtime manifests pin versions with no known PyPI advisories at
  release time. Their release workflows rerun a pinned `pip-audit` before
  certification or publish so a newly disclosed vulnerable dependency blocks
  rollout instead of silently entering production.
- The Cloudflare adapter lockfile has no known OSV advisories across its 94
  resolved npm packages. Its deployment gate runs a bounded, dependency-free
  OSV audit before the TypeScript check and publish step.
- The frontend is migrated to Next.js 16.3.4 and its exact lock has no known
  OSV advisories across 509 unique npm package versions. The release gate
  repeats that bounded audit and requires zero ESLint errors before building.
- No production database migration is required by this release.
- During the first 24 hours, compare newly-created subscriber and MCP work with
  this baseline. Notify on a new root-cause cluster, a stalled logical request,
  hidden MCP refusal, executor ownership contradiction, material failure-rate
  increase, billing inconsistency, or explicit negative feedback. Stay quiet
  when state is unchanged and non-actionable.
