# Request-based agent executor

The chat queue does not need another always-on Render machine. A dedicated
Cloud Run service named `valmera-agent` runs one `agent_turn` per HTTP request:

- request-based billing, `min-instances=0` — no idle instance charge;
- `max-instances=5`, concurrency 1 — up to five isolated turns at once;
- 2 vCPU / 4 GiB — native media state from one turn cannot kill another;
- the existing render executor still performs ffmpeg, indexing, capture,
  frames, tracking, matte, cleanup and stem separation.

The Render dispatcher still owns queue claiming and heartbeats. The agent
executor additionally heartbeats and commits the completed job, chat result and
credit charge itself. Therefore a Render deploy or broken HTTP response after
completion cannot discard the edit.

## Bootstrap once

Deploy the same thin application image as the render executors. Copy the
existing `valmera-executor` environment values for database, object storage,
model providers and `REMOTE_EXECUTOR_SECRET`, then add:

```text
WORKER_ROLE=agent_executor
REMOTE_EXECUTOR_URL=https://valmera-executor-950454325677.us-central1.run.app
```

Use these service settings:

```text
service: valmera-agent
region: us-central1
CPU: 2
memory: 4 GiB
concurrency: 1
minimum instances: 0
maximum instances: 5
request timeout: 3600 seconds
execution environment: gen2
billing: request-based
startup CPU boost: enabled
```

`REMOTE_AGENT_EXECUTOR_URL` is normally unnecessary. The dispatcher derives
the sibling URL from its existing `REMOTE_EXECUTOR_URL`. The successful health
response must report `"role":"agent_executor"` and the same `code_version` as
the render executor.

After bootstrap, `.github/workflows/deploy-executor.yml` deploys and verifies
all three Cloud Run services from one image. Its concurrency group cancels
obsolete builds before they create intermediate revisions.

## Render configuration

Keep the existing Render worker on `WORKER_ROLE=worker`. When the agent sibling
is available, that process automatically changes its agent lane from the local
memory-safe `WORKER_AGENT_SLOTS` value to five lightweight HTTP dispatch slots.
Media/index coordination, shorts and MCP remain unchanged.

The backend's queue notice uses `WORKER_AGENT_CAPACITY` (default 5), independent
of any one process's local slot count.

For cleaner Render deploys, set the background worker's maximum shutdown delay
to 300 seconds and:

```text
WORKER_SHUTDOWN_GRACE_S=280
```

The worker stops claiming before draining. Render starts the replacement first,
then the old process has time to finish ordinary work. The remote agent owns its
terminal write even if the old dispatcher eventually exits.

## Rollback

Set this explicit empty environment variable on the Render worker and redeploy:

```text
REMOTE_AGENT_EXECUTOR_URL=
```

An explicit empty value disables sibling discovery. Agent turns immediately
return to local execution at `WORKER_AGENT_SLOTS`; no database or frontend
rollback is required. The Cloud Run agent service then scales to zero.
