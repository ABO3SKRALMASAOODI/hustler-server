# Modal executor operations

Modal is the scale-to-zero compute plane. Render is the PostgreSQL dispatcher,
reaper, catalog publisher and health monitor; with Modal configured it never
downloads or decodes customer media and never runs ffmpeg, OpenCV or Chromium.
Cloud Run is an emergency launch fallback for legacy jobs only.

## One-time setup

1. Authenticate the local CLI: `modal setup`
2. Create the `main` environment if it does not exist.
3. Copy the current proven executor environment without printing secrets:
   `python worker/setup_modal_executor.py`
4. Apply the additive durable-verification schema from a trusted shell:
   `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/023_redesign_verification.sql`
5. Apply the provider ownership ledger **before** deploying the matching
   worker/executor code:
   `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/025_remote_executions.sql`
6. Deploy: `modal deploy worker/modal_app.py --env main`
7. Create a Modal deploy token and store it in GitHub secrets as
   `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`; also store the external
   production database DSN as `PRODUCTION_DATABASE_URL` for the read-only
   schema prerequisite.
8. Put the same invocation token on the Render worker, then set:

   ```text
   MODAL_EXECUTOR_ENABLED=1
   MODAL_EXECUTOR_PERCENT=100
   MODAL_EXECUTOR_APP=valmera-executor
   MODAL_EXECUTOR_ENVIRONMENT=main
   MODAL_CLOUD_RUN_FALLBACK=0
   EXECUTION_POLICY_MODE=legacy
   ```

The deployment workflow validates all model-visible skills, verifies the code
fingerprint, refuses to publish when migration 025 is absent or partial, and
warms/probes every US function and its promised runner set.
Only after that gate is green, switch `EXECUTION_POLICY_MODE=redesign` on
Render. Producers stamp the policy into every new root and child job; retries
and continuations keep that immutable owner. A durable Modal call reconnects by
call id and never silently falls back onto Render or launches duplicate paid
work. The remote input also waits for its exact Modal call ID to appear in the
ledger before downloading or encoding; if the dispatcher dies in the narrow
spawn-to-Postgres handoff window, that orphan exits before expensive work and
the ordinary queue lease can recover safely.

## Resource lanes

| Function | Modal resources | Cloud Run equivalent |
|---|---:|---:|
| `preview` | 2 physical cores, 2→4 GiB | 4 vCPU, 8 GiB |
| `batch` / `index` | 4 physical cores, 4→16 GiB | 8 vCPU, 16 GiB |
| `final` | 4 physical cores, 4→16 GiB, 6h envelope | 8 vCPU, 16 GiB |
| `light` | 1 physical core (2 burst limit), 1→4 GiB | lightweight frame inspection |
| `heavy` | 4 physical cores, 16→32 GiB | 8 vCPU, 32 GiB |
| `egress` | 1 physical core (2 burst limit), 1→4 GiB, US pinned | acquisition/streaming |
| `agent` | 0.125 reserved / 1 core limit, 1→2 GiB, concurrency 2, 6h envelope | 1 vCPU, 2 GiB |
| `mcp` | 0.125 reserved / 1 core limit, 1→2 GiB, concurrency 4, separate pool | none |
| `shorts` | 0.125 reserved / 1 core limit, 1→2 GiB, concurrency 2, separate pool | none |

An arrow is Modal's memory request→hard limit. Billing uses the greater of the
request or actual memory, while the old maximum remains available for an
outlier. CPU limits are unchanged. All functions stay US-pinned: a production
global-placement canary scheduled a customer final in `ap-northeast-2`, adding
unacceptable network and latency variance. Savings never depend on moving a
user's compute away from the proven data path.

Bounded `frames` uses `light`; browser `capture`, tracking, matting, matching,
cleanup and stems use `heavy`; `fetch`, stock acquisition and search use
`egress`. Studio agents, external MCP sessions and Shorts planning use three
independent autoscaling functions so one audience cannot consume another's
orchestration capacity. Every completed or failed input logs one `[resources]`
JSON record with cgroup-wide peak memory, CPU, bytes transferred, cold-start,
cache and cost telemetry, including child ffmpeg/Chromium processes.

All functions have zero minimum containers and no platform retries. Their
different maximum-container values are account runaway-cost ceilings, not
product concurrency slots: Modal queues temporary saturation. Interactive
compute retains the 3600-second limit. Durable `final`, `agent`, `mcp` and
`shorts` calls have six-hour platform envelopes and logical turns continue in
durable slices. Final FFmpeg time scales from authored program duration and
retains stall, runaway-output and lease watchdogs. Fenced PostgreSQL
completion, progress and retry classification are shared through
`executor_runtime.py`.

## Rollback

Set `EXECUTION_POLICY_MODE=legacy` on Render. No code deployment, EDL rollback
or data migration is required. Jobs already stamped `redesign` finish on the
new fleet; jobs created after the switch use legacy ownership. Keep Modal
enabled while redesign-stamped work drains. Disabling Modal is a separate
emergency action only after no redesign work remains.
