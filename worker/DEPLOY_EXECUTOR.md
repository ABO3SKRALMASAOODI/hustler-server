# Request-based media/index executor (round 38)

Moves the CPU-heavy work — `index`, `preview`, `final` — off the always-on
Render worker and onto a **scale-to-zero Google Cloud Run** service that spins
up a fresh 8-vCPU instance per job and costs **$0 while idle**. The Render
worker stays as a cheap **dispatcher**: it keeps the queue, retries, heartbeats,
reaper and credit-charging, runs `agent_turn` locally (network-bound), and just
hands render/index jobs to Cloud Run over HTTP.

Nothing here changes behavior until you (a) deploy the executor and (b) set two
env vars on the Render worker. To roll back, delete those two vars.

---

## 0. One image, two roles

The same `worker/` image runs as either role, chosen by `WORKER_ROLE`:

| Role | Where | `WORKER_ROLE` | Does |
|---|---|---|---|
| dispatcher (default) | existing Render worker | `worker` (or unset) | polls queue, runs agent turns, ships media/index to the executor |
| executor | new Cloud Run service | `executor` | runs `indexer`/`renderer` per HTTP request, scales to zero |

---

## 1. Deploy the executor to Cloud Run

Prereqs: a GCP project with billing on, and `gcloud` installed + `gcloud auth login`.

```bash
# generate a strong shared secret once; use the SAME value on both services
export EXEC_SECRET=$(openssl rand -hex 32)

cd hustler-server            # repo root (worker/Dockerfile builds the image)

gcloud run deploy valmera-executor \
  --source worker/ \
  --region us-central1 \
  --cpu 8 --memory 16Gi \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 5 \
  --timeout 1800 \
  --allow-unauthenticated \
  --set-env-vars "WORKER_ROLE=executor,REMOTE_EXECUTOR_SECRET=$EXEC_SECRET"
```

Then add **the rest of the env the render/index path needs** — copy them from
the current Render worker's Environment tab. At minimum:

```bash
gcloud run services update valmera-executor --region us-central1 \
  --update-env-vars \
DATABASE_URL="<Render Postgres EXTERNAL url>",\
S3_ENDPOINT="<R2 endpoint>",S3_ACCESS_KEY_ID="...",S3_SECRET_ACCESS_KEY="...",S3_BUCKET="...",\
OPENAI_API_KEY="<xAI key>",OPENAI_BASE_URL="https://api.x.ai/v1",\
DEEPGRAM_API_KEY="..."
```

Simplest and safest: mirror **every** env var from the Render worker onto the
executor, then just add `WORKER_ROLE=executor` + `REMOTE_EXECUTOR_SECRET`.
(`PIPELINE_VERSION` is a code constant now — don't set it as an env anywhere.)

Grab the service URL:

```bash
gcloud run services describe valmera-executor --region us-central1 \
  --format 'value(status.url)'      # -> https://valmera-executor-xxxx.a.run.app
```

Smoke-test it:

```bash
curl https://valmera-executor-xxxx.a.run.app/health      # -> {"status":"ok",...}
```

### Why these flags

- **`--concurrency 1`** — one job per instance so each render gets the full
  8 vCPU. This is what kills the old `INDEX_SLOTS=1` serialization: two
  uploads at once = two instances, not a queue.
- **`--min-instances 0`** — the whole point. Idle = $0. **Never raise this** to
  dodge cold starts, or that warm instance bills 24/7 (~$0.83/hr ≈ $600/mo) and
  you're back to renting.
- **`--max-instances 5`** — your cost ceiling and your parallelism ceiling.
  5 × 8 vCPU only ever runs when 5 jobs overlap; tune to taste.
- **`--timeout 1800`** — a wedge ceiling, not a headroom figure. It was 3600
  (Cloud Run's max) and two wedged finals ran it to the second, on a service
  billed per instance-second; the longest HEALTHY job on this hardware is
  ~300s, so 1800 is still 6x. The dispatcher's own timeout
  (`REMOTE_EXECUTOR_TIMEOUT_S`, default **1500s**) must always sit UNDER this,
  so that a wedged job is killed by the executor shortly after the dispatcher
  gave up on it — otherwise the orphan keeps burning 8 vCPU next to the retry.
  **Change the two together**; the dispatcher side is a code constant in
  `worker/config.py`, deliberately not a per-service env var (see
  `PIPELINE_VERSION` for what env drift on a paired constant costs).
- **`--allow-unauthenticated`** — Render isn't in GCP's IAM, so the endpoint is
  public but guarded by the bearer secret (`/run` returns 401 without it). See
  "Hardening" below to lock it down further later.
- **`--memory 16Gi`** — Cloud Run's `/tmp` is an **in-memory** filesystem, and a
  job downloads the ORIGINAL video there (plus writes the proxy, plus whisper
  holds ~2 GB if it runs locally). 16 GiB comfortably fits one short-form job.
  If very large uploads OOM the instance, raise memory (and CPU scales with it),
  or mount a Cloud Run gen2 volume for the workdir. Because `--concurrency 1`,
  only ever one job's files sit in memory at a time.

---

## 2. Point the Render worker (dispatcher) at it

On the existing Render **worker** service, add:

```
REMOTE_EXECUTOR_URL     = https://valmera-executor-xxxx.a.run.app
REMOTE_EXECUTOR_SECRET  = <the same $EXEC_SECRET>
```

and, since dispatcher "slots" are now just threads awaiting HTTP (nearly free),
raise the media/index fan-out so jobs dispatch in parallel:

```
WORKER_MEDIA_SLOTS = 4
WORKER_INDEX_SLOTS = 4
WORKER_AGENT_SLOTS = 2     # unchanged; agent turns still run here
```

Redeploy. The worker log should print:

```
valmera-worker (dispatcher) starting: ... media/index=remote executor https://...
```

Because the heavy encoding left this box, you can also **downsize the Render
worker** to its cheapest instance — it now only orchestrates and runs
network-bound agent turns.

---

## 3. Verify, then roll back if needed

- Upload a video → watch the executor's Cloud Run logs show `start index …` /
  `done index …`, and the studio's analyze % advance as before (progress still
  flows through Postgres).
- Make an edit → a `preview` job runs on the executor; the studio plays it.
- **Rollback is instant:** remove `REMOTE_EXECUTOR_URL` from the Render worker
  and redeploy — media/index run locally again, exactly as before. The Cloud
  Run service can sit idle at $0 or be deleted.

---

## 4. Hardening (optional, later)

The bearer secret over HTTPS guards compute/cost; the body is only a job id +
payload (the executor reads real data from the DB). To go further:

- Rotate `REMOTE_EXECUTOR_SECRET` on both services together.
- Put the executor behind **Cloud Run IAM** (`--no-allow-unauthenticated`) and
  call it with a Google-signed ID token — needs a GCP service-account key on the
  Render side, so it's more setup; the secret is fine for now.
- Restrict ingress once you front it with a load balancer.

---

## Cost sanity check

L4/8-vCPU is ~$0.83/hr **metered per-second**. A full editing session
(index + a few previews + a final) is ~15–20 compute-minutes ≈ **$0.20–0.30**,
and $0 when nobody's working. Break-even vs the old $25/mo flat worker is
~90 sessions/month — everything below that is cheaper *and* far faster.
