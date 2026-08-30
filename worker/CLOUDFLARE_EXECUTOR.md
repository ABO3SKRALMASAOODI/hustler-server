# Cloudflare Containers migration

Cloudflare is Valmera's primary compute plane, not a second rendering
implementation. It runs `executor_runtime.py`, the same renderer, lease checks,
terminal database commit, storage path, retry classifier and resource
telemetry used by the legacy adapters. Provider selection is stamped into each
claimed job, so a percentage change affects new work only.

The Worker routes named calls over fixed interactive, batch, Studio-agent,
MCP, and Shorts pools. A novel Container ID cold-starts on Cloudflare, so
using a call ID as the instance ID would throw away Python/image/source-cache
warmth on every job. Call state remains keyed by the deterministic call ID
inside its shard. A busy shard refuses the new call before `/run`; an ambiguous
call reconnects to the exact same named call and never launches duplicate
compute.

The interactive image omits the baked multi-gigabyte Whisper model because
its admitted job types never transcribe. The batch image retains that model
for index fallback. Both still use the same Python renderer and source tree;
this only removes irrelevant cold-start bytes from the user-facing lane.
Terminal call envelopes remain reconnectable for seven days, then each shard
prunes them in bounded batches after later completions. Active and ambiguous
calls are never age-pruned; this prevents unbounded Durable Object storage
growth without sacrificing restart recovery.

## Resource admission (there is no one-hour limit)

Cloudflare's self-serve maximum is 4 vCPU, 12 GiB RAM and 20 GB disk. Source
duration is not a Cloudflare resource limit. The old `3600`-second rule was a
Valmera rollout guard and incorrectly rejected a 30-second EDL merely because
it came from a three-hour podcast. Queue-backed work is now admitted by staged
input bytes (4 GiB by default), while the renderer can use R2 ranged reads when
an individual object is larger than local scratch.

Interactive rendering stays on the slim standard-3 image. Indexing, capture,
tracking, matting, cleanup, stem separation and media acquisition use the
standard-4 batch image, which carries their Chromium and model dependencies.
Agent, MCP and Shorts orchestration remain isolated, and every synchronous
media child routes back through the Worker instead of enabling Modal inside
the container. `CLOUDFLARE_MAX_SOURCE_DURATION_S` is an optional emergency
brake only; `0` disables it.

The production topology exposes 20 interactive, 8 batch, 5 Studio-agent, 20
MCP and 8 Shorts shards. Dispatcher wait slots use the same Cloudflare-aware
defaults, so a 20-card editorial run is not silently serialized to the old
three-call remote limit.

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
   database and S3/R2 values, and OpenAI/vision/image keys. Cloudflare does not
   require Modal credentials.
4. Run the manual `deploy-cloudflare-executor` workflow. It type-checks the
   Worker, refuses to publish unless migration 025 is complete, builds both
   Container sizes, installs required secrets, deletes the four obsolete Modal
   bindings, and verifies provider identity plus the exact commit fingerprint.
   Publishing does not route any production traffic.
5. On Render, make Cloudflare the complete execution owner:

   ```text
   CLOUDFLARE_EXECUTOR_ENABLED=1
   CLOUDFLARE_EXECUTOR_URL=https://<worker>.workers.dev
   CLOUDFLARE_EXECUTOR_PERCENT=100
   CLOUDFLARE_EXECUTOR_TYPES=preview,preview_check,final,index,filmstrip,agent_turn,mcp_tool,shorts_plan,capture,frames,track,matte,smatch,clean,stems,fetch,search,stock_acquire,ytprobe,mcp_media
   CLOUDFLARE_SYNCHRONOUS_TYPES=capture,frames,track,matte,smatch,clean,stems,fetch,search,stock_acquire,ytprobe,mcp_media
   CLOUDFLARE_MODAL_FALLBACK=0
   CLOUDFLARE_MAX_INPUT_BYTES=4294967296
   CLOUDFLARE_STREAM_SOURCE_MIN_DURATION_S=3600
   CLOUDFLARE_MAX_SOURCE_DURATION_S=0
   CLOUDFLARE_TIMEOUT_FINAL_S=21600
   CLOUDFLARE_TIMEOUT_INDEX_S=21600
   MODAL_EXECUTOR_ENABLED=0
   MODAL_EXECUTOR_PERCENT=0
   ```

   Apply these only after the workflow's five lane probes pass. A queue lease
   stamped for Modal before cutover can move to Cloudflare only when Modal
   rejects it before launch and no active provider ledger exists; once either
   provider accepts compute, ownership remains immutable.

## Performance-gated canary

For a future provider or image change, use 5%, 10%, 25%, 50% and 100% canary
stages for new jobs. Keep each stage until it contains enough real warm and
cold jobs to compare like-for-like media shapes. Advance only when:

- terminal failure rate is not higher;
- p50 and p95 `queue_wait_s + provider_start_s + total_s` are no more than
  5% slower;
- p95 executor `total_s` is no more than 5% slower;
- no Cloudflare OOM, disk-capacity, lost-lease, duplicate-call, or deadline
  event occurred;
- rendered proof/verification metadata and storage registration are complete.

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

## Rollback

Keep the Worker deployed until `remote_executions` has no active Cloudflare
rows. Already-stamped Cloudflare jobs must finish on their named Container.
An emergency rollback requires deliberately re-enabling and configuring a
tested alternate provider before setting `CLOUDFLARE_EXECUTOR_PERCENT=0`; the
redesign startup check refuses to start when any required job family has no
remote owner. No EDL, credit, asset or database rollback is required.
