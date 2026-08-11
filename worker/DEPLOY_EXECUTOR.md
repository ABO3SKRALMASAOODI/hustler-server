# Scale-to-zero executor fleet

> **COST/RELIABILITY UPDATE (Aug 2026).** Interactive previews remain on the
> request service, while `final` and `index` run as one-shot Cloud Run Jobs.
> The Jobs lane is configured with **zero platform retries** and 8 vCPU / 16
> GiB, so a dispatcher restart cannot duplicate paid work and a deterministic
> bad EDL/ffmpeg command cannot replay for another hour. The shared application
> policy permits at most one retry for a genuinely transient failure. Invalid
> preview EDLs are not retried unchanged: the agent receives one structured
> repair pass and must write a new EDL version before rendering again.
>
> `valmera-agent` now uses the agent-only image and 1 vCPU / 2 GiB at
> concurrency 2. It omits executor-only browser/model payloads, reducing cold
> start time and letting two network-bound LLM turns share an instance. All
> services and the Job still scale to zero.

> **ROUND 97: DEPLOYS ARE AUTOMATIC.** `.github/workflows/deploy-executor.yml`
> redeploys this service from every push to main that touches `worker/`, then
> fails the run unless `/health` reports the pushed commit's own
> `version.code_version()` — the manual step below is now the FALLBACK, not
> the process. The workflow needs two one-time grants that must be run by a
> human (they mint credentials):
>
> ```bash
> # 1) let the deploy SA act as the executor's runtime service account
> gcloud iam service-accounts add-iam-policy-binding \
>   950454325677-compute@developer.gserviceaccount.com \
>   --member serviceAccount:gh-executor-deploy@valmera.iam.gserviceaccount.com \
>   --role roles/iam.serviceAccountUser --project valmera
>
> # 2) put the SA key into the repo secret (key file from
> #    `gcloud iam service-accounts keys create key.json
> #       --iam-account gh-executor-deploy@valmera.iam.gserviceaccount.com`)
> gh secret set GCP_SA_KEY -R ABO3SKRALMASAOODI/hustler-server < key.json
> ```
>
> Until the secret exists the workflow skips with a warning instead of
> failing, and this file's manual command keeps working.

Moves the CPU-heavy work — `index`, `preview`, `final` — off the always-on
Render worker and onto a **scale-to-zero Google Cloud Run** service that spins
up a fresh 8-vCPU instance per job and costs **$0 while idle**. The Render
worker stays as a cheap **dispatcher**: it keeps the queue, retries, heartbeats,
reaper and credit-charging, runs `agent_turn` locally (network-bound), and just
hands render/index jobs to Cloud Run over HTTP.

Nothing here changes behavior until you (a) deploy the executor and (b) set two
env vars on the Render worker. To roll back, delete those two vars.

---

## 0. Four roles, purpose-built runtime images

The worker source runs in four production roles, chosen by `WORKER_ROLE`.
Compute roles share the full executor image; the agent role uses the slim
agent image built by `Dockerfile.agent-base` / `Dockerfile.agent-runtime`.

| Role | Where | `WORKER_ROLE` | Does |
|---|---|---|---|
| dispatcher (default) | existing Render worker | `worker` (or unset) | polls queue and ships work to request-based executors |
| executor | Cloud Run services | `executor` | interactive preview/tool compute per request, scales to zero |
| agent executor | Cloud Run service | `agent_executor` | runs isolated agent turns with concurrency 2, scales to zero |
| batch executor | Cloud Run Job | `batch_executor` | owns one final/index through terminal DB commit; platform retries are zero |

`valmera-batch-launcher` is a tiny authenticated scale-to-zero bridge. Render
calls it because Render cannot directly mint Google IAM tokens. The launcher
starts the Job with an immutable queue-row/claim payload; it performs no media
work and adds no idle monthly charge.

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
  --no-cpu-boost \
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

Production also has a right-sized preview service. It keeps the same 8 vCPU
but uses 8 GiB because previews read the 540p proxy rather than staging the
full-resolution original. For the standard Cloud Run service names, the worker
derives this sibling URL automatically from `REMOTE_EXECUTOR_URL`; no Render
dashboard change is required. The optional explicit override is:

```
REMOTE_EXECUTOR_PREVIEW_URL = https://valmera-executor-preview-xxxx.a.run.app
```

Set the variable to an empty value to disable the derived preview route and
send previews to `REMOTE_EXECUTOR_URL` exactly as before. Finals, indexes and
the heavyweight tool runners always stay on the 32 GiB service.

and, since dispatcher "slots" are now just threads awaiting HTTP (nearly free),
raise the media/index fan-out so jobs dispatch in parallel:

```
WORKER_MEDIA_SLOTS = 4
WORKER_INDEX_SLOTS = 4
WORKER_AGENT_SLOTS = 2     # local rollback capacity
```

When `valmera-agent` is bootstrapped, the dispatcher auto-discovers it and
uses five HTTP dispatch slots instead. See `DEPLOY_AGENT_EXECUTOR.md`.

Redeploy. The worker log should print:

```
valmera-worker (dispatcher) starting: ... media/index=remote executor https://...
```

Because the heavy encoding left this box, you can also **downsize the Render
worker** to its cheapest instance — it now only orchestrates and runs
network-bound agent turns.

The GitHub workflow builds the multi-gigabyte dependency/model base only when
`worker/Dockerfile` or `worker/requirements.txt` changes. Normal source pushes
publish a thin source layer, deploy both services, disable the no-op 8→8 startup
CPU boost, and retain only the three newest images after two days.

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

## 3c. ffmpeg 8.1 static — DEFAULT since Aug 6 2026 (round 91)

The image carries the BtbN static ffmpeg 8.1 (Dockerfile `ARG
FFMPEG_STATIC=1`) — the line every dev machine and itest runs. Verified on a
no-traffic canary (revision 00090, tag ffcanary) before the default flipped:

- speed: the same 59s graded program (project 110 v192) encoded in **20.6s**
  on 8.1 vs **~29s** on 5.1 — four warm samples within 0.07s of each other,
  measured through the real /run path with real storage;
- captions: the result sheet showed dynamic emphasized words, title cards
  and styled text pixel-correct — the static build reads system fontconfig
  (DejaVu + Noto) exactly like the apt build did.

To unwind a future ffmpeg surprise: build once with `FFMPEG_STATIC=0` (edit
the ARG default — `gcloud run deploy` has no `--build-arg`), or route
traffic back to a pre-flip revision. The canary recipe, for the next
upgrade: deploy `--no-traffic --tag ffcanary`, POST forced preview jobs for
a caption-heavy project at the tag URL, LOOK at the result sheet, compare
`encode_s`, then `gcloud run services update-traffic … --to-latest`.

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
