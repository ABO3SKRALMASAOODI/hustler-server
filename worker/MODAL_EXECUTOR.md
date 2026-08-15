# Modal executor operations

Modal is the primary, cheaper scale-to-zero executor. Cloud Run remains a
launch-time fallback while the rollout is measured and can be restored as the
primary by setting `MODAL_EXECUTOR_ENABLED=0` on Render.

## One-time setup

1. Authenticate the local CLI: `modal setup`
2. Create the `main` environment if it does not exist.
3. Copy the current proven executor environment without printing secrets:
   `python worker/setup_modal_executor.py`
4. Deploy: `modal deploy worker/modal_app.py --env main`
5. Create a Modal deploy token and store it in GitHub secrets as
   `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`.
6. Put the same invocation token on the Render worker, then set:

   ```text
   MODAL_EXECUTOR_ENABLED=1
   MODAL_EXECUTOR_PERCENT=10
   MODAL_EXECUTOR_APP=valmera-executor
   MODAL_EXECUTOR_ENVIRONMENT=main
   MODAL_CLOUD_RUN_FALLBACK=1
   ```

Raise the percentage only after live p95 runtime and failure rate meet the
Cloud Run baseline. Selection is stable per job, so retries do not randomly
switch providers. A Modal submission can fall back to Cloud Run only before a
durable Modal call id exists; after that point the dispatcher reconnects to the
same call and never buys a duplicate render.

## Resource lanes

| Function | Modal resources | Cloud Run equivalent |
|---|---:|---:|
| `preview` | 2 physical cores, 2→4 GiB | 4 vCPU, 8 GiB |
| `batch` | 4 physical cores, 8→16 GiB | 8 vCPU, 16 GiB |
| `light` | 4 physical cores, 8→32 GiB | heavy fallback previously used |
| `heavy` | 4 physical cores, 16→32 GiB | 8 vCPU, 32 GiB |
| `egress` | 4 physical cores, 8→32 GiB, US pinned | heavy fallback previously used |
| `agent` | 0.125 reserved / 1 core limit, 1 GiB, concurrency 4 | 1 vCPU, 2 GiB, concurrency 4 |

An arrow is Modal's memory request→hard limit. Billing uses the greater of the
request or actual memory, while the old maximum remains available for an
outlier. CPU limits are unchanged. Compute functions are globally scheduled to
avoid the 1.5× explicit-region surcharge and widen cold-start capacity; the
latency-sensitive agent and the URL/YouTube egress lane stay US-pinned.

`frames` and `capture` use `light`; `fetch` and `search` use `egress`;
tracking, matting, matching, cleanup, and stems retain `heavy`. Every completed
or failed input logs one `[resources]` JSON record with cgroup-wide memory and
CPU telemetry, including child ffmpeg/Chromium processes.

All functions have zero minimum containers, at most five containers, no
platform retries, and a 3600-second execution limit. The executor's existing
fenced Postgres completion, progress, retry classification, and ffmpeg timeout
logic are shared with Cloud Run through `executor_runtime.py`.

## Rollback

Set `MODAL_EXECUTOR_ENABLED=0` on Render. No code deployment and no data
migration are required; the existing `REMOTE_EXECUTOR_*` Cloud Run routes take
over immediately after Render restarts the worker.
