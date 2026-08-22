# Cloudflare Containers migration

Cloudflare is the primary capacity-safe compute plane beside Modal, not a second rendering
implementation. Both providers run `executor_runtime.py`, the same renderer,
lease checks, terminal database commit, storage path, retry classifier and
resource telemetry. Provider selection is stamped into each claimed job, so a
percentage change affects new work only.

The Worker routes named calls over fixed interactive, batch, Studio-agent,
MCP, and Shorts pools. A novel Container ID cold-starts on Cloudflare, so
using a call ID as the instance ID would throw away Python/image/source-cache
warmth on every job. Call state remains keyed by the deterministic call ID
inside its shard. A busy shard refuses the new call before `/run`, allowing the
proven Modal lane to keep that user moving. An ambiguous call never switches
providers; a definitively terminal provider/capacity failure may hand the same
still-running queue lease to Modal exactly once.

The interactive image omits the baked multi-gigabyte Whisper model because
its admitted job types never transcribe. The batch image retains that model
for index fallback. Both still use the same Python renderer and source tree;
this only removes irrelevant cold-start bytes from the user-facing lane.
Terminal call envelopes remain reconnectable for seven days, then each shard
prunes them in bounded batches after later completions. Active and ambiguous
calls are never age-pruned; this prevents unbounded Durable Object storage
growth without sacrificing restart recovery.

## Why Modal remains a capacity fallback

Cloudflare's self-serve maximum is 4 vCPU, 12 GiB RAM and 20 GB disk. Production
telemetry has already observed heavy effects above that memory envelope.
Queue-backed preview, proof, final, index, filmstrip, agent, MCP, and Shorts
jobs are Cloudflare eligible; byte-heavy render/index work is capacity-gated
to at most 4 GiB of project input and one hour of source duration. Agent, MCP,
and Shorts get isolated 4-GiB orchestration images and continue to offload
their synchronous 16-32-GiB effects, capture, tracking, matting, cleanup,
stems, and acquisition calls to Modal.

This guarantees that savings never come from silently giving a user fewer
resources. A Cloudflare launch that fails before `/run` falls back to Modal.
Once a named call may exist, the dispatcher and restart guardian reconnect to
that exact call. Only a terminal envelope with a still-current queue lease can
replace the failed ownership record and run once on Modal.

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
   database and S3/R2 values, OpenAI/vision/image keys, and Modal token values
   needed by orchestration fallback.
4. Run the manual `deploy-cloudflare-executor` workflow. It type-checks the
   Worker, refuses to publish unless migration 025 is complete, builds both
   Container sizes, installs secrets, and verifies provider identity plus the
   exact commit fingerprint. Publishing does not route any production traffic.
5. On Render, configure the URL and start at zero percent:

   ```text
   CLOUDFLARE_EXECUTOR_ENABLED=1
   CLOUDFLARE_EXECUTOR_URL=https://<worker>.workers.dev
   CLOUDFLARE_EXECUTOR_PERCENT=0
   CLOUDFLARE_EXECUTOR_TYPES=preview,preview_check,final,index,filmstrip,agent_turn,mcp_tool,shorts_plan
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
list rates, plus the mandatory $5 Workers Paid fee amortized over the observed
successful-job rate. Included usage is ignored, so a pass does not depend on
temporary allowance and is deliberately conservative.

For an operator-side check using the same implementation:

```bash
DATABASE_URL="$PRODUCTION_DATABASE_URL" \
python worker/executor_canary_gate.py \
  --hours 24 --expected-percent 5 --min-samples 20 \
  --report cloudflare-canary-gate.json
```

Heavy synchronous operations with no queue identity remain Modal-only. Never
force those operations into the current self-serve Container sizes merely to
raise the Cloudflare percentage.

## Rollback

Set `CLOUDFLARE_EXECUTOR_PERCENT=0`. New unstamped jobs stay on Modal; already
stamped Cloudflare jobs finish on their named Container and remain protected by
the durable ownership ledger. Keep the Worker deployed until
`remote_executions` has no active Cloudflare rows. No EDL, credit, asset or
database rollback is required.
