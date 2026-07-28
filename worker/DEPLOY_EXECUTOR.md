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
  --cpu 8 --memory 32Gi \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 5 \
  --timeout 3600 \
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
- **`--timeout 3600`** — Cloud Run's maximum, and now genuinely needed. Encodes
  measured on this service run **0.5–1.8× realtime**, and it is the
  FILTERGRAPH that costs rather than the pixels (a 270×480 source still
  encoded at 0.93× realtime), so an hour-long programme needs a budget in the
  thousands of seconds. It was lowered to 1800 in round 48 after two wedged
  finals ran the old cap to the second — but the thing that actually catches a
  wedge is `media.run()`'s stall watchdog, which fires on ffmpeg emitting no
  progress at all, long before any of these ceilings. Treat these as the
  backstop, not the defence.
  The dispatcher's own timeouts (`config.REMOTE_EXECUTOR_TIMEOUTS`, per job
  kind: preview 1500 / final 3400 / index 3400) must always sit UNDER this, so
  that a wedged job is killed by the executor shortly after the dispatcher gave
  up on it — otherwise the orphan keeps burning 8 vCPU next to the retry.
  **Change them together**; the dispatcher side is a code constant in
  `worker/config.py`, deliberately not a per-service env var (see
  `PIPELINE_VERSION` for what env drift on a paired constant costs), and
  `worker/tests/test_executor_timeouts.py` FAILS if this doc and those
  constants disagree.
- **`--allow-unauthenticated`** — Render isn't in GCP's IAM, so the endpoint is
  public but guarded by the bearer secret (`/run` returns 401 without it). See
  "Hardening" below to lock it down further later.
- **`--execution-environment gen2`** — REQUIRED, and not for speed. On **gen1**
  the workdir is a gVisor overlay whose `statvfs` reports the **host's** disk:
  the live executor claimed **1001 GB free on a 32 GiB instance**. Any capacity
  check that believed it would wave through a job that then gets the container
  OOM-killed — silently, because an OOM produces no error at all. On gen2 the
  same call reports **32.0 GB**, exactly the memory limit, which is the truth.
  Verify with `curl .../health | jq .workdir` — `fstype` and `free_gb` are
  reported precisely so this is checkable instead of assumed.
- **`--memory 32Gi`** — this is **the flag that sets the maximum video length**,
  because the workdir is sized by it. `storage.MAX_UPLOAD_GB` (14) is derived
  from it: 32 GiB / `WORKDIR_HEADROOM` 2.2 = 14.5 GB, which `/health` reports as
  `max_source_gb`. **Raising the upload cap without raising this first just
  moves the refusal to after the user has spent 40 minutes uploading.** 32Gi is
  Cloud Run's per-instance maximum, so going beyond ~14 GB sources means
  mounting a volume for `WORKER_TMP_DIR`, not more RAM. Memory is the cheap
  axis: ~$0.00008/s against ~$0.000192/s for 8 vCPU.
- **(historical)** Cloud Run's `/tmp` is an **in-memory** filesystem, and a
  job downloads the ORIGINAL video there (plus writes the proxy, plus whisper
  holds ~2 GB if it runs locally). This is the flag that decides how long a
  video the product can accept: at 16Gi the upload cap could not safely exceed
  ~6 GB, which is a 1-hour recording at 13 Mbps — below what an ordinary phone
  or camera produces, so "we support 3-hour videos" was never true. 32Gi (the
  per-instance maximum) covers the 16 GB upload cap with room for the render
  output beside it. Memory is the cheap axis here: 8 vCPU costs ~$0.000192/s
  against ~$0.00008/s for 32 GiB, so this is roughly a 20% bump on instance
  cost, not a doubling.
  **Past 32Gi the answer is a volume, not more RAM** — mount one for
  `WORKER_TMP_DIR` and the workdir stops being memory at all. Either way the
  job no longer dies silently: `storage.check_workdir_capacity` measures the
  free space before staging a source and fails with a message naming this flag,
  because an OOM kill on Cloud Run produces no error at all — the container
  just dies and the dispatcher records "Worker died and retries are exhausted".
  Because `--concurrency 1`, only ever one job's files sit in memory at a time.

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

## 3b. REDEPLOY THIS AFTER EVERY PUSH THAT TOUCHES `worker/`

**This is the single most dangerous property of the split.** Render redeploys
the dispatcher automatically on push to `main`. Cloud Run does not. So a normal
push updates the queue, the agent and the tools while leaving the code that
actually makes the pixels on whatever was built last — and the two halves
disagree silently. It has cost real customers twice:

| | What the executor was missing | What users got |
|---|---|---|
| round 53 | the code that writes the `trans_v` render stamp | **41 of 41** finished exports undownloadable; one customer pressed Download 17 times |
| round 55 | round 52's `_output_clock` + the new `stylize` kinds | a 150.48s edit exported at 158.58s and failed verification **3 times**; his previews rejected outright as "EDL shape invalid" |

Both looked like ordinary render bugs. Neither was: the fixes were written,
tested, pushed and live on the dispatcher the whole time.

Redeploy (env vars are preserved across a `--source` deploy):

```bash
cd hustler-server
gcloud run deploy valmera-executor --source worker/ --region us-central1
```

### Check which code each side is running

`/health` reports a fingerprint of the executor's worker source — no auth, no
setup, one curl:

```bash
curl -s "$(gcloud run services describe valmera-executor --region us-central1 \
  --format 'value(status.url)')/health"
# {"status":"ok","role":"executor","code_version":"016bcde97910",
#  "pipeline_version":7,"outro_version":2,...}
```

Compare with the dispatcher's, which it prints on every boot
(`valmera-worker (dispatcher) starting: code=…`) and re-checks against the
executor in the same line. Locally: `cd worker && python -c "import version;
print(version.code_version())"`.

The fingerprint is a hash of the top-level `worker/*.py`, so it moves for **any**
code change — unlike `PIPELINE_VERSION` / `OUTRO_VERSION` / `TRANSITION_VERSION`
/ `TIMELINE_MEDIA_VERSION`, which only catch skew when somebody remembers to
bump them, and neither incident above involved a bump.

**The check never withholds work.** A skewed executor keeps getting jobs — it
renders most things correctly, and refusing to use it would take the product
down to prevent a subset of edits from being wrong. It only ever *says so*: a
loud dispatcher boot line, and the skew appended to the error of any remote job
that fails, so it lands in the admin job list next to the failure it explains.

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
