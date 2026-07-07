# Valmera — Agentic AI Video Editor (backend + worker)

Users upload a video and chat: *"cut the dead air, caption it, make the intro
punchier."* An LLM agent reads a precomputed text index of the video, edits an
Edit Decision List (EDL) through validated tools, renders a preview, takes
revisions in the same chat, and — on explicit confirmation — renders the final
full-resolution video. The agent never touches pixels; FFmpeg does. The
original file is never modified.

## Architecture

| Piece | Where | What |
|---|---|---|
| Frontend | Vercel (`startup_frontend` repo) | Next.js studio: chat left, video workspace right |
| API | Render Web Service (`backend/`) | Flask. Projects, presigned uploads, chat → jobs. Never touches media bytes |
| Worker | Render **Background Worker** (`worker/`, Docker) | ffmpeg + faster-whisper + PySceneDetect + the agent loop |
| Queue | `video_jobs` table in Render Postgres | `FOR UPDATE SKIP LOCKED` polling, heartbeats, retries (max 2) |
| Storage | Cloudflare R2 (any S3-compatible) | originals/ proxies/ audio/ thumbs/ sheets/ renders/ |
| LLM | Any OpenAI-compatible endpoint | default DashScope: qwen-plus (agent) + qwen-vl-plus (vision) |

Two phases, per the core rules:

1. **Index (once per file, cached forever by sha256):** probe → 720p proxy
   (VFR→CFR) + 16 kHz wav → whisper word timestamps → silencedetect → shot
   detection + thumbnails → contact sheets → optional vision captions →
   one JSON index in the `indexes` table.
2. **Agent turns (one per chat message):** OpenAI tool-calling loop over
   READ tools (`get_transcript`, `search_transcript`, `get_shots`,
   `find_silences`, `look_at`), WRITE tools (`keep_segments`, `add_captions`,
   `add_music`, `set_volume` — each creates a new EDL version), and META
   tools (`get_edl`, `render_preview`, `ask_user`). Every tool call is
   persisted as an `activity` chat message. Previews render at 480p from the
   proxy; finals render from the original, only via the user-confirmed
   `POST /projects/:id/render/final`.

All timestamps everywhere are seconds as floats. The EDL schema lives in
`worker/schemas.py` (Pydantic) and is mirrored at `src/types/edl.ts` in the
frontend repo.

## Environment variables

| Var | Used by | Meaning / default |
|---|---|---|
| `DATABASE_URL` | api, worker | Render Postgres URL |
| `S3_ENDPOINT` | api, worker | e.g. `https://<account_id>.r2.cloudflarestorage.com` |
| `S3_PUBLIC_ENDPOINT` | api | Optional. Endpoint embedded in presigned URLs when it differs from `S3_ENDPOINT` (needed for docker-compose MinIO; leave unset on R2) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | api, worker | R2 API token pair |
| `S3_BUCKET` | api, worker | bucket name |
| `S3_REGION` | api, worker | `auto` for R2 (default) |
| `OPENAI_BASE_URL` | worker | default `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| `OPENAI_API_KEY` | api*, worker | DashScope key. *api only checks its presence to gate chat |
| `AGENT_MODEL` | worker | default `qwen-plus` |
| `VISION_MODEL` | worker | default `qwen-vl-plus`; set empty to disable all vision |
| `WHISPER_MODEL` | worker | default `small` |
| `WHISPER_DEVICE` | worker | `cpu` (default, int8) or `cuda` — pointing at a GPU box needs no code change |
| `MAX_UPLOAD_GB` | api, worker | default `2` |
| `MAX_DURATION_S` | worker | default 3h |
| `PUBLIC_APP_URL` | (reserved) | `https://valmera.io` |
| `MESSAGES_PER_HOUR` | api | default 20 per project |
| `MAX_CONCURRENT_JOBS_PER_USER` | api | default 3 |
| `WORKER_MEDIA_SLOTS` / `WORKER_AGENT_SLOTS` | worker | default 1 / 2 |

## Production setup (one-time)

1. **R2 bucket:** Cloudflare dashboard → R2 → create bucket (e.g.
   `valmera-media`) → create an API token with Object Read & Write → note
   Account ID, Access Key ID, Secret. `S3_ENDPOINT` is
   `https://<account_id>.r2.cloudflarestorage.com`.
2. **Bucket CORS** (browsers PUT/GET directly via presigned URLs):
   ```bash
   S3_ENDPOINT=... S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=... S3_BUCKET=... \
   python scripts/setup_bucket_cors.py
   ```
   (Origins `https://valmera.io`, `https://www.valmera.io`, localhost:3000;
   `ExposeHeaders: ETag` is required for multipart.)
3. **DashScope key:** Alibaba Cloud Model Studio → API keys. Uses the
   international endpoint by default.
4. **API env:** add the `S3_*` vars + `OPENAI_API_KEY` to the existing Render
   Web Service. (It already has `DATABASE_URL`.) Redeploy.
5. **Worker:** Render → New → **Background Worker** → this repo →
   Runtime **Docker** → Root Directory `worker` (Dockerfile is
   `worker/Dockerfile`). Instance: **Standard (2 GB) or larger** — whisper
   `small` int8 peaks around 1 GB. Env: `DATABASE_URL`, all `S3_*`,
   `OPENAI_*`, `AGENT_MODEL`, `VISION_MODEL`, `WHISPER_MODEL`,
   `WHISPER_DEVICE=cpu`.
6. **DB schema:** already applied (additive) via
   `backend/migrations/001_video_editor.sql`. Re-running is safe:
   ```bash
   psql "$DATABASE_URL" -f backend/migrations/001_video_editor.sql
   ```

## Local development

**With Docker:**
```bash
cp .env.example .env          # add OPENAI_API_KEY, or leave empty and use the fake LLM
docker compose up --build     # postgres + minio + api (:5001) + worker
docker compose exec api python /repo/scripts/integration_test.py
```

**Without Docker (macOS/Linux; needs postgres binaries + ffmpeg on PATH and a
python with `backend/requirements.txt` + `worker/requirements.txt` +
`moto[server]` installed):**
```bash
bash scripts/run_local_integration.sh
```
This boots a throwaway Postgres, a moto S3 server, `scripts/fake_llm.py`
(a scripted OpenAI-compatible editor so no LLM key is needed), and the real
worker — then runs `scripts/integration_test.py`, which exercises the whole
path: presigned upload → index (>3 shots, >3 silences on the synthesized
test clip) → chat "remove the silences and add captions" → agent writes a new
EDL → preview render shorter than the source → confirmation-gated final
render at source resolution.

`scripts/make_test_video.py` synthesizes the ~60s test clip (6 colored
slates, speech bursts via `say`/`espeak-ng` when available, real ≥1s
silences).

## Job types

`video_jobs.type` ∈ `index | preview | final | agent_turn` with states
`queued | running | done | failed`, `progress` 0–100, `error` text,
heartbeats every 20s, stale-job requeue after 120s, max 2 retries for media
jobs (agent turns are never auto-retried — the user just resends).

## Legacy

The old app-builder pipeline (`engine/AA.py`, `outputs/`, `/auth/generate`,
`jobs` table) is untouched and still deployed; the new editor lives entirely
in the tables/routes/services above.
