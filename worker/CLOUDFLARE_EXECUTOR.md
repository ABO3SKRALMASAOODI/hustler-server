# Cloudflare Containers migration

Cloudflare is a canary compute plane beside Modal, not a second rendering
implementation. Both providers run `executor_runtime.py`, the same renderer,
lease checks, terminal database commit, storage path, retry classifier and
resource telemetry. Provider selection is stamped into each claimed job, so a
percentage change affects new work only.

The Worker routes named calls over a fixed five-instance interactive pool and
three-instance batch pool. A novel Container ID cold-starts on Cloudflare, so
using a call ID as the instance ID would throw away Python/image/source-cache
warmth on every job. Call state remains keyed by the deterministic call ID
inside its shard. A busy shard refuses the new call before `/run`, allowing the
proven Modal lane to keep that user moving; an accepted or ambiguous call can
never switch providers.

The interactive image omits the baked multi-gigabyte Whisper model because
its admitted job types never transcribe. The batch image retains that model
for index fallback. Both still use the same Python renderer and source tree;
this only removes irrelevant cold-start bytes from the user-facing lane.

## Why the rollout is hybrid

Cloudflare's self-serve maximum is 4 vCPU, 12 GiB RAM and 20 GB disk. Production
telemetry has already observed heavy effects above that memory envelope and
Modal's 4-physical-core batch lane can be faster than 4 vCPU. Therefore the
initial eligible set is `preview_check,filmstrip,index`, capacity-gated to at
most 4 GiB of project input and one hour of source duration. Preview and final
exist in the adapter for controlled benchmarks, but are not canary defaults.
Agent, MCP, Shorts, capture, tracking, matting, cleanup, stems and acquisition
remain on Modal.

This is how the migration guarantees that savings never come from silently
giving a user fewer resources. A Cloudflare launch that is proven to have
failed before `/run` may fall back to Modal. Once a named Container call may
exist, the dispatcher and restart guardian reconnect to that exact call; they
never launch a duplicate on another provider.

## One-time setup

1. Enable Workers Paid and Containers in the Cloudflare account.
2. Apply the additive ownership ledger before deploying routing code:

   ```bash
   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
     -f backend/migrations/025_remote_executions.sql
   ```

3. Add GitHub secrets used by
   `.github/workflows/deploy-cloudflare-executor.yml`: Cloudflare API token and
   account ID, `CLOUDFLARE_EXECUTOR_URL`, `REMOTE_EXECUTOR_SECRET`, production
   database and S3/R2 values, plus OpenAI/vision values needed by indexing.
4. Run the manual `deploy-cloudflare-executor` workflow. It type-checks the
   Worker, builds both Container sizes, installs secrets, and verifies provider
   identity plus the exact commit fingerprint. Publishing does not route any
   production traffic.
5. On Render, configure the URL and start at zero percent:

   ```text
   CLOUDFLARE_EXECUTOR_ENABLED=1
   CLOUDFLARE_EXECUTOR_URL=https://<worker>.workers.dev
   CLOUDFLARE_EXECUTOR_PERCENT=0
   CLOUDFLARE_EXECUTOR_TYPES=preview_check,filmstrip,index
   CLOUDFLARE_MODAL_FALLBACK=1
   CLOUDFLARE_MAX_INPUT_BYTES=4294967296
   CLOUDFLARE_MAX_SOURCE_DURATION_S=3600
   ```

## Performance-gated canary

Increase only new-job routing to 5%, then 10%, 25%, 50% and 100% for the
eligible types. Keep each stage until it contains enough real warm and cold
jobs to compare like-for-like media shapes. Advance only when all of these are
true against Modal for the same job type and input-size/duration band:

- terminal failure rate is not higher;
- p50 and p95 `queue_wait_s + provider_start_s + total_s` are no more than
  5% slower;
- p95 executor `total_s` is no more than 5% slower;
- no Cloudflare OOM, disk-capacity, lost-lease, duplicate-call, or deadline
  event occurred;
- rendered proof/verification metadata and storage registration are complete;
- direct monthly cost per successful job is lower after Workers Paid fees.

Cold-start, queue, CPU, peak sampled memory, disk/bytes, provider and cache-hit
evidence are already persisted in `video_jobs.result.timings` and emitted in
`[resources]` logs. Do not compare an index to a preview or a 30-second proxy
to a 4K hour-long source.

Run the manual `gate-cloudflare-canary` workflow before every percentage
increase. It queries production read-only and uploads an aggregate report; it
fails closed when any job type or matched input cohort lacks evidence, when
the remote ownership ledger is contradictory, or when reliability, artifact,
warm/cold, p50/p95, runner, fallback, capacity or gross-cost gates fail. It
never changes traffic itself. Its cost estimate uses measured active CPU plus
provisioned memory/disk and uploaded-byte egress at Cloudflare's published
list rates; included Workers Paid usage is ignored, so a pass does not depend
on temporary free allowance.

For an operator-side check using the same implementation:

```bash
DATABASE_URL="$PRODUCTION_DATABASE_URL" \
python worker/executor_canary_gate.py \
  --hours 24 --expected-percent 5 --min-samples 20 \
  --report cloudflare-canary-gate.json
```

Only after the eligible set passes should `preview` be added. Add `final` only
after a separate representative export benchmark passes the same gates; retain
Modal fallback for finals that cross Cloudflare's input/resource envelope.
Never add heavy or orchestration families to the current self-serve Container
sizes.

## Rollback

Set `CLOUDFLARE_EXECUTOR_PERCENT=0`. New unstamped jobs stay on Modal; already
stamped Cloudflare jobs finish on their named Container and remain protected by
the durable ownership ledger. Keep the Worker deployed until
`remote_executions` has no active Cloudflare rows. No EDL, credit, asset or
database rollback is required.
