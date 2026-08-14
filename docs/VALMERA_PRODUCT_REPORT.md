# Valmera — Complete Product & Technical Report

**The agentic AI video editor: how every part of it works today**

*Prepared July 23, 2026 · Based on a full read of both repositories (`hustler-server` backend/worker/engine and `frontend-next` studio) at their current state on disk.*

> **Current-state note (August 14, 2026):** §23 is the authoritative delta
> audit for the live agent, media senses, token spike, and quality roadmap.
> Several older inventory statements in §§1–22 describe the July build (for
> example the single Render renderer and bundled music/SFX packs) and are kept
> as engineering history, not current production truth.

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [System Topology & Infrastructure](#2-system-topology--infrastructure)
3. [The Life of a Video: End-to-End Flow](#3-the-life-of-a-video-end-to-end-flow)
4. [The Index: How Valmera Understands a Video](#4-the-index-how-valmera-understands-a-video)
5. [The EDL: The Data Model Every Edit Lives In](#5-the-edl-the-data-model-every-edit-lives-in)
6. [Timeline Math: Three Clocks and the Remapping Engine](#6-timeline-math-three-clocks-and-the-remapping-engine)
7. [The Agent: Loop, Context, Prompt, and the Honesty Layer](#7-the-agent-loop-context-prompt-and-the-honesty-layer)
8. [The Complete Tool Catalog (Everything the Agent Can Do)](#8-the-complete-tool-catalog)
9. [Captions: The Full System](#9-captions-the-full-system)
10. [The Text & Motion-Graphics Layer](#10-the-text--motion-graphics-layer)
11. [Effects, Transitions, Zooms, Speed & Grades — Full Inventory](#11-effects-transitions-zooms-speed--grades)
12. [Audio: Four Layers, Music Library, SFX, Ducking, Mastering](#12-audio-four-layers-music-library-sfx-ducking-mastering)
13. [Generative Tools: Images, Video, Sound, URL Fetching](#13-generative-tools-images-video-sound-url-fetching)
14. [Rendering & Encoding: How Pixels Actually Get Made](#14-rendering--encoding-how-pixels-actually-get-made)
15. [Backend API, Database, Storage & Credits](#15-backend-api-database-storage--credits)
16. [Worker Orchestration: Jobs, Lanes, Retries, Crash Recovery](#16-worker-orchestration-jobs-lanes-retries-crash-recovery)
17. [The Studio Frontend](#17-the-studio-frontend)
18. [Observability, Admin & Testing](#18-observability-admin--testing)
19. [Security Posture](#19-security-posture)
20. [The Complete Limitations Catalog](#20-the-complete-limitations-catalog)
21. [Docs vs. Reality: What You Promise vs. What Ships](#21-docs-vs-reality)
22. [Where to Take It: Improvement Opportunities Grounded in the Code](#22-where-to-take-it)
23. [August 14 Current-State Agent & Quality Audit](#23-august-14-current-state-agent--quality-audit)

---

# 1. Executive Overview

Valmera is an **agentic AI video editor**. A user uploads raw footage, the system builds a machine-readable understanding of that footage (the **index**), and from then on the user edits by talking. An LLM agent — currently xAI's Grok 4.5 — receives each chat message, reads the index, and edits by calling tools that mutate a JSON **Edit Decision List (EDL)**. The agent never touches pixels. A separate **renderer** compiles any EDL version into an actual MP4 with a single giant ffmpeg invocation. The user watches server-rendered previews in the browser, can also drag a few things around directly on a timeline, and exports a full-quality final when happy.

The product's single most distinctive engineering idea is **enforced honesty**: the system is built, at every layer, to make it impossible (or at least very hard) for the agent to claim it did something it didn't do. This shows up as:

- Every tool call returns a machine-verifiable result string (`"EDL v3 -> v4: ..."`, `"NO CHANGE"`, `"REJECTED"`).
- A regex-based post-processor inspects the agent's final reply for edit/render claims and compares them against what the tools actually did; fabricated claims trigger a forced regeneration, and a persistent fabricator gets its reply discarded and replaced by a system-authored fallback.
- The system prompt itself is rewritten at runtime to remove claims about features that aren't deployed (no music pack shipped → all "built-in library" prose is deleted from the prompt).
- Every render gets a vision-model self-check, a mid-word-cut audit, a caption audit, and a repetition audit stamped into its result.

The second distinctive idea is **determinism and immutability**: EDL versions are append-only; renders are cache-keyed on (EDL version, source hash, caption fingerprint); caption/graphics compilation is byte-stable; legacy field semantics are frozen forever, with new behavior riding new fields. This is what makes "step back through your edit history" and render caching actually safe.

The third is the **war-story codebase**: there are almost no TODO comments anywhere; instead, nearly every guard, constant, and odd-looking branch carries a postmortem comment citing the specific production incident it fixes (the 63-minute queue, the infinite re-index loop, the OOM SIGKILL, the render that played in R2 but not in Safari). This report preserves those where they explain why things are the way they are.

**Two important honesty notes about the product itself:**

- The root `CLAUDE.md` and frontend `AGENTS.md` still largely describe the **retired app-builder product** (the "describe an app, AI builds it" pivot predecessor). Those routes (`/auth/generate`, `engine/AA.py`, the `engine/Agent*.py` family, the `Pipeline/` Vite template) are still deployed but dormant. Everything in this report is about the live video product: `backend/routes/video.py`, `backend/routes/admin_video.py`, and the entire `worker/` service.
- There is **no text-to-speech** anywhere in the codebase despite "voiceover" being a feature — voiceover means "a user-uploaded audio file laid over the program." `eleven.py` is ElevenLabs *sound-effect generation* (text → whoosh), not speech.

### Scale of the codebase (video product only)

| Component | File | Lines |
|---|---|---|
| Agent tools | `worker/agent_tools.py` | 4,903 |
| Studio UI | `frontend-next/src/app/studio/page.js` | 3,899 |
| Backend video API | `backend/routes/video.py` | 2,165 |
| Renderer | `worker/renderer.py` | 1,798 |
| EDL schema | `worker/schemas.py` | 1,612 |
| Captions | `worker/captions.py` | 1,332 |
| Agent loop | `worker/agent_loop.py` | 1,058 |
| Admin observability | `backend/routes/admin_video.py` | 995 |
| DB layer (worker) | `worker/db.py` | 582 |
| Indexer | `worker/indexer.py` | 552 |
| SSRF-hardened fetcher | `worker/net_fetch.py` | 528 |
| URL media import | `worker/url_media.py` | 520 |
| Text graphics | `worker/graphics.py` | 510 |
| Perception (beats/stress) | `worker/perception.py` | 460 |
| Timeline math | `worker/timeline.py` | 454 |
| EDL type mirror (frontend) | `src/types/edl.ts` | 434 |

---

# 2. System Topology & Infrastructure

## 2.1 The four deployed pieces

| Piece | Where | What it does |
|---|---|---|
| **Frontend** | Vercel — `https://valmera.io` | Next.js 15 App Router. Landing/SEO/docs pages + the studio. All API calls proxied through a `/api-backend/` rewrite in `next.config.js` (never calls Render directly, except direct browser↔R2 media traffic). |
| **Backend API** | Render Web Service — `entrepreneur-bot-backend.onrender.com` | Flask + Gunicorn (**3 sync workers**, timeout 600). Stores pointers and JSON, enqueues jobs, presigns URLs. **Never touches media bytes, never runs ffmpeg or the agent loop.** Also still hosts the legacy app-builder routes. |
| **Worker** | Render Background Worker (Docker, `worker/Dockerfile`) | A pure poller (no HTTP server). Runs everything heavy: indexing (ffmpeg + Deepgram/whisper + PySceneDetect + vision), the agent loop, preview renders, final renders. python:3.11-slim + ffmpeg + fontconfig + DejaVu/Noto/Noto-CJK fonts. The faster-whisper `medium` model (~0.5 GB) is **baked into the Docker image at build time** (`ARG WHISPER_MODEL=medium`, downloaded into `HF_HOME=/opt/hf`) so it isn't re-downloaded on container start. |
| **Data plane** | Render managed PostgreSQL + Cloudflare R2 (S3-compatible; MinIO/moto in dev) | One Postgres DB shared by API and worker (the job queue IS a Postgres table). All media in R2, always via presigned URLs. |

Supporting services: **Deepgram** (transcription, nova-3), **xAI** (Grok 4.5 for the agent + vision, grok-2-image-1212 for image gen), **ElevenLabs** (sound-effect generation), **fal.ai** (Kling 2.5 Turbo Pro for AI video), **Paddle** (payments), **Brevo** (email), **Google Analytics/Search Console**.

## 2.2 The critical shared-code trick

The backend **imports worker code by file path** (`importlib.util.spec_from_file_location`) — specifically `worker/schemas.py` and `worker/timeline.py`. This makes EDL validation, `keep_boundaries`, `program_duration`, and `remap_program_items` **byte-identical** between the API (which applies user timeline drags) and the worker (which applies agent edits). The frontend carries a **third copy**: a hand-written JS mirror of `timeline.py` inside the studio page, contract-tested against `worker/tests/timeline_golden.json` ("the studio JS mirror MUST reproduce these exactly; a mismatch means the two repos drifted"). Three implementations, one source of truth, pinned by golden vectors.

## 2.3 PIPELINE_VERSION: the lesson that shaped the deployment model

`PIPELINE_VERSION = 7` lives as a **code constant in `worker/schemas.py`** — deliberately NOT an environment variable. It stamps every index row; a cache hit requires both matching sha256 and matching pipeline version. It used to be an env var set per-service; on July 16–17, 2026, the two services' values drifted for a day, and every project open triggered a 30–90-minute re-index that *still wrote the old version* — an infinite loop that starved real customers. The postmortem principle now embedded in the code: **"Constants deploy atomically; env vars don't."** The backend additionally caps self-heal re-indexes at fewer than 3 index jobs per project per 6 hours, so no future mismatch can starve the worker again.

## 2.4 Capacity reality (the current hard ceiling)

The worker runs on roughly **one vCPU**. Concurrency is governed by three "lanes" (threads polling the job table):

- `WORKER_MEDIA_SLOTS = 1` — preview + final renders (one at a time, globally, across all customers)
- `WORKER_INDEX_SLOTS = 1` — indexing (one at a time, globally)
- `WORKER_AGENT_SLOTS = 2` — agent turns (IO-bound LLM loops)

Measured production numbers, documented in the repo: a 19-minute upload costs **~16.5 minutes to index** (down from 47 minutes before Deepgram; the 540p proxy encode is now ~88% of index time) and **~14 minutes per preview render**, one at a time. One real customer queued **63 minutes** behind another user's video and left. The repo's own conclusion: raising slot counts on 1 vCPU makes both jobs slower and doubles memory; **the real fix is a bigger instance.** This is the single most important operational fact about the product today.

## 2.5 Environment/config surface (worker `config.py`, complete)

Infrastructure: `DATABASE_URL`, `S3_ENDPOINT/ACCESS_KEY_ID/SECRET/BUCKET/REGION` (required — `require_core()` refuses to boot without them), `TMP_DIR=/tmp/valmera`, `POLL_INTERVAL_S=2.0`.

LLM: `OPENAI_BASE_URL=https://api.x.ai/v1`, `OPENAI_API_KEY` (an xAI key), `AGENT_MODEL=grok-4.5`, `VISION_MODEL=grok-4.5`, `LLM_TIMEOUT_S=90`, `LLM_MAX_RETRIES=1`, `VISION_TIMEOUT_S=120` ("spiky grok multimodal latency").

Generative: `IMAGE_GEN_MODEL=grok-2-image-1212` (the fully-dated id; the bare `grok-2-image` is not a valid xAI id and 404'd silently for a week until July 22, 2026), `IMAGE_EDIT_MODEL`/`IMAGE_API_URL` (DashScope restyle path, empty on xAI), `IMAGE_TIMEOUT_S=150`, `MAX_GENERATED_IMAGES_PER_TURN=8`; `ELEVENLABS_API_KEY`, `SFX_MAX_DURATION_S=22`, `SFX_TIMEOUT_S=60`, `MAX_GENERATED_SFX_PER_TURN=10`; `FAL_KEY`, `VIDEO_GEN_MODEL=fal-ai/kling-video/v2.5-turbo/pro/image-to-video`, `VIDEO_MAX_SECONDS=10`, `VIDEO_POLL_TIMEOUT_S=240`, `VIDEO_POLL_INTERVAL_S=6`, `MAX_GENERATED_VIDEOS_PER_TURN=3`.

Transcription: `DEEPGRAM_API_KEY`, `TRANSCRIBER` (auto: deepgram if key set), `DEEPGRAM_MODEL=nova-3`, `DEEPGRAM_TIMEOUT_S=300`; whisper fallback `WHISPER_MODEL=medium`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8`, `WHISPER_BEAM_SIZE=5`, `WHISPER_HOTWORDS="Valmera, valmera.io"`, `WHISPER_COMPRESSION_RATIO_THRESHOLD=None` (deliberately disabled — see §4.3).

Quotas & fetching: `MAX_UPLOAD_GB=2`, `MAX_DURATION_S=10800` (3h), `URL_FETCH_ENABLED=1`, `URL_FETCH_EXTRACTOR=1` (separate yt-dlp kill switch — "direct links and platform extraction are the same feature technically and very different legally"), `FETCH_MAX_BYTES=500MB`, per-kind caps (clip 500MB / audio 50MB / image 10MB), `FETCH_TIMEOUT_S=180`, `FETCH_MAX_DURATION_S=3600`, `FETCH_MAX_HEIGHT=1080`, `MAX_FETCHED_URLS_PER_TURN=8`.

Agent loop: `AGENT_MAX_ITERATIONS=30`, `AGENT_TEMPERATURE=0.2`, `AGENT_TURN_TIMEOUT_S=450`, `PREVIEW_WAIT_TIMEOUT_S=900`, `TOOL_OUTPUT_CHAR_BUDGET=12000`, `TRANSCRIPT_CHAR_BUDGET=48000`, `FULL_INDEX_MAX_DURATION_S=600`, `FULL_INDEX_MAX_CHARS=40000`, `AGENT_TURN_BUDGET_GRACE=3`.

Pricing (must be kept in sync across `config.py`, `db.py`, and `admin_video.py` — triplicated by design flaw): `LLM_PRICE_IN_PER_M=2.0`, `LLM_PRICE_OUT_PER_M=6.0`, `IMAGE_PRICE_USD=0.07`, `SFX_PRICE_USD=0.08`, `VIDEO_BASE_PRICE_USD=0.35` (first 5s) + `VIDEO_PRICE_USD_PER_SEC=0.07`.

Encoding: `PROXY_HEIGHT=540`, `PROXY_PRESET=veryfast`, `PROXY_CRF=25`; `PREVIEW_PRESET=ultrafast`; `FINAL_PRESET=veryfast`, `FINAL_CRF=20`; `SILENCE_NOISE_DB=-35dB`, `SILENCE_MIN_S=0.6`, `SCENE_THRESHOLD=27.0`, `MAX_VISION_SHEETS=12`; render verification `RENDER_DURATION_TOLERANCE_S=0.75`/`FRAC=0.03`, `RENDER_BLACK_MAX_RATIO=0.7`; outro `OUTRO_DURATION_S=2.5`, fades 0.45/0.35, audio tail fade 0.25, `OUTRO_ON_PREVIEW=0`, `OUTRO_VERSION=1`; `FFMPEG_TIMEOUT_S=5400`, `FFMPEG_STALL_TIMEOUT_S=300`.

Worker: `MEDIA_SLOTS=1`, `INDEX_SLOTS=1`, `AGENT_SLOTS=2`, `HEARTBEAT_EVERY_S=20`, `STALE_AFTER_S=120`, `MAX_ATTEMPTS_MEDIA=3`, `MAX_ATTEMPTS_AGENT=1`.

---

# 3. The Life of a Video: End-to-End Flow

This is the complete happy path, with every subsystem it touches:

1. **Upload.** The browser validates extension + size client-side, asks `POST /projects/:id/uploads` for a presigned URL (single PUT ≤64MB, presigned multipart with 64MB parts above), uploads **directly to R2** (the API never proxies bytes), then calls `POST /uploads/complete`. The server verifies the key belongs to the project, HEADs the object, re-checks size, sniffs the first 64 bytes for magic-byte/kind mismatches (a JPEG renamed `.mp4` gets a clean 400), inserts the asset row under a project-row lock (dedupe against racing completes), and enqueues an `index` job.
2. **Indexing** (§4). The worker's index lane claims the job: download → sha256 → probe → 540p proxy + 16kHz WAV → Deepgram/whisper transcription with word timestamps → silence detection → shot detection → contact sheets → vision captioning of shots → beat/energy/stress perception → assemble a `VideoIndex` JSON and upsert it keyed by content hash. If the same bytes were ever indexed anywhere (any project, any account), it's a **free cache hit**.
3. **Ready.** `_finish_setup` seeds **EDL v1** (`keep = [[0, duration]]` — keep everything), and posts an LLM-authored "your video is ready" chat notice (with a regex guard that discards any draft falsely claiming edits already happened). If the user sent a message *during* indexing, the turn **auto-starts** — "nobody resends."
4. **Chat → agent turn** (§7). Each user message becomes an `agent_turn` job. The agent gets the system prompt + auto-generated capabilities digest + a project-state block (for videos ≤10 minutes, the **entire transcript and every shot description are inlined into context**) + the last 20 chat messages. It loops up to 30 iterations calling tools, each of which validates and writes a new immutable EDL version. It finishes by rendering a preview (or the system auto-renders for it), passes the honesty gauntlet, and replies.
5. **Preview.** `render_preview` enqueues a `preview` job; the renderer compiles the EDL into one ffmpeg filtergraph, rendering **from the 540p proxy, capped at 480p, x264 ultrafast CRF 27** — fast and sacrificial. The result is cache-keyed so re-rendering an unchanged version is instant. The studio polls `/state`, sees the new preview, attaches it, and muted-autoplays once so burned captions visibly move.
6. **Direct manipulation** (§17). The user can also drag inserts/music/voiceover on the timeline, change aspect ratio with pills, and fix transcript words inline. These go through `POST /projects/:id/edl {op,args}`, which applies the same validation and remapping code the agent uses, appends a `created_by:'user'` version, and auto-renders a preview.
7. **Export.** Only the user can trigger a final (`POST /render/final`) — the agent is explicitly told it cannot. Finals render **from the original upload** at source resolution, x264 veryfast CRF 20, AAC 192k, and always end with the ~2.5s branded Valmera end card. The studio's Download button auto-downloads when the render lands.
8. **Billing.** After each agent turn, the worker sums that turn's `llm_calls` (tokens at $2/$6 per M, images $0.07, SFX/video at real cost), converts at 1 credit = $0.01, floors at 1 credit, and deducts daily → bonus → monthly pools. Indexing, previews, finals, and pre-index concierge chat are **never charged**.

There is a second, main-video-less path: **canvas projects**, where the user starts from nothing ("make me a 9:16 video from these images") and the program is a gapless sequence of generated/uploaded images and clips over a colored canvas.

---

# 4. The Index: How Valmera Understands a Video

The index is the product's perception system — computed **once per unique file** (sha256-keyed, shared across all projects and accounts), after which "the agent works from text forever after." It's job type `index`, run in a dedicated lane so a multi-minute analysis can never wedge interactive previews (that starvation was documented as the #1 cause of "I chatted and nothing happened" churn).

## 4.1 The stages, in order (with progress % written to the job row)

1. **Download + sha256** (8%).
2. **Probe** (12%) — `media.probe()` returns *what a player shows, not what the container claims*: display dimensions with rotation display-matrix applied (a phone's 1284×2778 coded-portrait becomes real portrait), container `duration` AND separate `video_duration` (the picture track's own length — iOS screen recordings can carry 2.37s of video in a 16.65s container), fps (avg → r → 30 fallback), VFR detection, SAR sanity-clamped to [0.1, 10]. Duration over 3 hours fails the job.
3. **Cache check** — a hit requires matching sha256 **and** `pipeline_version == 7`. On hit, the project still gets its own proxy (copied server-side from a donor project if needed) and setup finishes immediately.
4. **Proxy + WAV** (12→35%) — 540p H.264 (`veryfast`, CRF 25, yuv420p, AAC 128k, `+faststart`, CFR-normalized). The proxy is then **measured**: if its picture track runs short by more than max(0.4s, 2%), it is re-encoded with `tpad=stop_mode=clone` to hold the last frame — the proxy must faithfully mimic player behavior. WAV: mono 16 kHz PCM for ASR.
5. **Transcription** (60%) — §4.3. Word timings are clamped into [0, duration] (`clamp_word_times`) because ASR on music hallucinates words past EOF (a real 16.65s clip produced a "word" spanning 15.36–34.72s). Then sentence grouping.
6. **Silence detection** (65%) — ffmpeg `silencedetect=noise=-35dB:d=0.6`; failure degrades to `[]` **with a recorded warning**, never silently.
7. **Shots + thumbnails** (75%) — PySceneDetect `ContentDetector(threshold=27.0)` on the proxy; failure degrades to one full-length shot with a warning. One 320px-wide thumbnail per shot at its temporal midpoint, via a hardened extractor that verifies the frame file actually exists (Debian ffmpeg 5.x exits **zero** on empty seeks — one missing thumbnail once killed an entire index).
8. **Contact sheets + vision** (85%) — §4.4.
9. **Artifact upload** (92%) — proxy → `proxies/{project}/{sha}.mp4` (hard failure if this fails), WAV → `audio/...`, thumbs → `thumbs/{project}/{sha}/shot_{id}.jpg`, sheets → `sheets/...` (each individually degradable to warnings).
10. **Perception sidecar** (94%) — §4.5. Runs inline for new indexes; pre-existing indexes get it lazily on first use, deliberately **without** a pipeline-version bump (shipping perception re-indexed nothing).
11. **Assemble + persist** — the `VideoIndex` is upserted into the `indexes` Postgres table (`ON CONFLICT (video_sha256) DO UPDATE`).

## 4.2 What the index contains (`VideoIndex`, exact shape)

```
version: 1
video:     { duration, fps, width, height, has_audio, vfr_normalized }
shots:     [ { id, start, end, thumb_key, caption: {setting, people, action, on_screen_text} } ]
words:     [ { w, t0, t1 } ]                          # word-level, 3-decimal seconds
sentences: [ { id: "s1"..., text, t0, t1, wi0, wi1 } ] # wi0/wi1 index into words[]
silences:  [ [t0, t1], ... ]
sheet_keys: [ ... ]
language:  "en" | ...
warnings:  [ str ]        # every non-fatal degradation, surfaced in admin
perception: { v, bpm, bpm_conf, beats[], energy[], energy_bin_s, vb_env[], vb_env_fps } | null
```

Notable absences: **no waveform** beyond the perception envelopes, **no diarization** (sentences are speaker-agnostic), **no filler-word detection at index time** (fillers are found at edit time by matching transcript tokens), **no object detection / face tracking / OCR beyond what the vision model reads off contact sheets**.

## 4.3 Transcription in detail

**Deepgram nova-3 is the production default** whenever `DEEPGRAM_API_KEY` is set. One POST of the whole WAV to `/v1/listen` with `model=nova-3&smart_format=true&punctuate=true&detect_language=true` plus repeated `keyterm` params from the brand vocabulary ("Valmera", "valmera.io"). Two retries with backoff, only on network errors / 429 / 5xx (a 4xx fails immediately — a bad key will fail identically forever). Missing word timestamps **raise** rather than return empty, because "an empty transcript is indistinguishable from 'this video has no speech,' which is a claim we then make to the user's face." Proven impact, documented in the repo: on a real 19.3-minute video, transcription went from **1742s (whisper medium) to 1.53s**, cut the whole index from 47 to 16.5 minutes, and found 45 words whisper missed.

**faster-whisper `medium` (CPU, int8)** is the automatic fallback, and it records a user-visible warning on the index when it runs. Decode settings: `word_timestamps=True`, VAD with 500ms min-silence and 400ms speech padding (padding keeps VAD from shaving word tails), beam 5, temperature ladder 0.0→1.0, `condition_on_previous_text=False` (one misheard proper noun can't snowball). The most interesting setting is `compression_ratio_threshold=None` — **deliberately disabled**. The library's default of 2.4 treats a speaker's legitimately repeated takes as looping hallucination and silently collapses them. Measured gzip ratios in the code comments: normal speech ~1.4, three repeats ~3.05, five repeats ~4.99. Since retake-heavy raw footage is the product's headline use case, and an eaten repeat is invisible while a hallucinated loop is visible and fixable, the threshold is off. After a fallback run, the model is **explicitly released from memory** — keeping the ~1.5 GB model resident on a fallback-only path is what OOM-killed the worker in production (job 204: transcribe 19 minutes, then a proxy encode + preview render + agent turn in the same process → SIGKILL).

**Sentence grouping**: break on terminal punctuation, on inter-word pauses > 0.6s, or at hard caps of 12 words / 6.0 seconds — "so the transcript panel can never show a run-on line."

## 4.4 Visual perception (contact sheets + vision LLM)

Exactly **one frame per shot** (temporal midpoint) is sampled, at 320px wide, from the proxy. Frames are tiled into **5×5 contact sheets** (320×180 tiles + 24px label strips, JPEG q82), each tile labeled `#<shot id> <m:ss>-<m:ss>`. These sheets are the ONLY thing the vision model ever sees during indexing — **one call per sheet, never per frame**. The prompt asks for a strict JSON array: for every tile, `{shot, setting, people, action, on_screen_text}` (truncated to 200/200/300/300 chars respectively).

The cost cap: `MAX_VISION_SHEETS=12` → at most 12 vision calls = 300 shot descriptions per video. A shot-heavy 3-hour video gets its sheets sampled evenly and a warning recorded: "visual captioning sampled N of M contact sheets to bound cost — some shots have no visual description." Vision failure degrades to a warning; vision is optional everywhere in the system.

## 4.5 Perception: beats, energy, and word stress

`perception.py` computes, from the WAV, with **streaming STFT** (never a full spectrogram in RAM — "a 20-min spectrogram is ~400 MB in float64, on a worker that has OOM-crashed before"):

- **BPM + confidence**: spectral-flux onset envelope → FFT autocorrelation with unbiased correction → log-normal tempo prior centered at 110 BPM → parabolic interpolation, with careful octave-folding logic in both directions (down while >140 BPM if the half-lag ACF supports it; up when ≤100 if the double-tempo ACF is ≥75%). Confidence blends salience, stability across track quarters, and absolute ACF height.
- **Beat grid** (only when confidence ≥ 0.3): 64 phase candidates, each beat refined to a local envelope peak within ±15% of a lag only if it beats the grid point by >5% — "a rigid grid drifts on human-played music; a pure peak-picker loses the meter — this does neither." Frame timestamps are at window *centers*, which moved the grid from ~70ms early to inside AV-sync tolerance.
- **Energy curve**: mean power per 0.5s bin, in dB relative to track peak.
- **Speech-band envelope** (300–3400 Hz), max-pooled to 8 Hz for storage.
- **Word stress**: per-word 0–1 score = the word's envelope peak against a rolling ~10s local ceiling (so a speaker who warms up doesn't get every late word scored "stressed"). Feeds `punch_in_on_emphasis`, `sound_design_pass`, and `suggest_emphasis`.

The design invariant, stated in the code: **"Perception feeds DECISIONS, never renders."** Tools use it to *choose* concrete timestamps that get written into the EDL; the renderer never consults it, so renders stay reproducible from (EDL version, source sha, index words) alone. It has its own version key (`PERCEPTION_VERSION=1`) independent of `PIPELINE_VERSION`, and stale sidecars recompute lazily rather than triggering re-indexes.

## 4.6 Re-index triggers and self-healing

On every project open, `/state` self-heals, bounded to <3 index jobs per project per 6h and serialized by a project-row lock: (1) index built by an older pipeline version → background re-index (the old index keeps serving; chat stays **quiet**, because a real customer once got a second "Your video is ready… I haven't made any edits" greeting over a session where the agent had already cut her video); (2) last index job failed with no index row → re-enqueue, making "re-open the project to try again" literally true; (3) a shared-sha cache hit whose per-project setup died → re-run.

---

# 5. The EDL: The Data Model Every Edit Lives In

The EDL (Edit Decision List) is a pure-JSON document. Every edit — agent or user — produces a **new, immutable, validated version** in the `edls` table (`UNIQUE(project_id, version)`, append-only, `created_by ∈ {user, agent}`). The agent's core mantra from the system prompt: *"You edit by modifying an Edit Decision List through tools — you never touch pixels; the renderer does."*

## 5.1 Top-level document

```python
EDL:
  keep:      [[start, end], ...]      # source seconds; sorted, non-overlapping, spans ≥ 0.05s
  canvas:    Canvas | None            # set iff keep == [] (no-main-video "canvas" program)
  captions:  CaptionsFromTranscript | [CaptionItem] | None    # 3-way union
  music:     [MusicItem]
  sfx:       [SfxItem]
  volume:    [VolumeItem]             # source-time gain automation on the speaker track
  frame:     Frame | None             # aspect ratio + crop/pad/pad_blur
  inserts:   [InsertItem]             # clips/images spliced INTO the timeline
  voiceover: [VoiceoverItem]
  effects:   Effects | None           # grade, zooms, fades, transition, censor regions, stylize, grade_custom
  overlays:  [OverlayItem]            # PIP layer (round 35)
  texts:     [TextItem]               # motion-graphics text layer (round 35)
  vectors:   [VectorItem]             # renderer-native shapes / indicators
  speed:     [SpeedSpan]              # source-time speed ramps (round 35)
  master:    Master | None            # loudness mastering (round 35)
```

Two valid shapes: a **main-video program** (non-empty `keep`, validated against source duration) or a **canvas program** (empty `keep` + `canvas`; needs ≥1 insert; inserts are re-laid gaplessly end-to-end; source-time features — volume, from_transcript captions, censor regions, speed, non-source frame — are forbidden). Canonical canvas dimensions: 16:9→1920×1080, 9:16→1080×1920, 1:1→1080×1080, 4:5→1080×1350, 4:3→1440×1080; fps default 30; bg_color default #000000.

## 5.2 Versioning philosophy: signature stability instead of version numbers

There is **no version field on the EDL document itself**. Backward compatibility rests on `edl_signature()` — a canonical JSON string with all `None`/`[]`/`{}` recursively dropped and keys sorted. Every new field defaults to empty, so any EDL written before a field existed hashes byte-identically forever (pinned by `test_legacy_signature_stable_and_no_v2_leak`). Neutral values collapse to `None` during validation (opacity 1.0, cx 0.5, intensity 0.5, size_scale 1.0, all-neutral grade_custom, `frame.ratio="source"`, linear ease…), which is what makes **"NO CHANGE" detection** possible: a write that produces a byte-identical signature creates no version row and returns *"NO CHANGE — … Do NOT tell the user you changed anything."*

The corollary is a hard cultural rule visible all over the code: **the render-time meaning of a stored field can never change.** When the karaoke word-group clamp needed raising from 4, the fix was a *new* field (`karaoke_group_n`) baked at write time — three stored production EDLs render under the frozen legacy clamp forever. When music looping shipped, `loop` was made opt-**in**, because defaulting it on would have changed historic audio without a version bump.

## 5.3 Every collection, with exact ranges and defaults

**keep** — sorted `[start, end]` source-second spans, min 0.05s, ends ≤ source duration + 0.01, overlap tolerance 0.001s. Times rounded to 0.01s everywhere.

**CaptionsFromTranscript** — `{mode: "from_transcript", max_words_per_caption ∈ [1,16], karaoke_group_n ∈ [1,8] | None, style: CaptionStyle, emphasis_words: ≤60 strings}`. **CaptionItem** (manual/dictated) — `{text, start, end, style?}` in source time.

**CaptionStyle** — the full field set: `color` #RRGGBB (default #FFFFFF), `size ∈ s/m/l/xl` (base px 30/40/52/68 at the 1280×720 reference frame), `size_scale ∈ [0.5, 3.0]`, `position ∈ bottom/top/middle` (ASS alignments 2/8/5, MarginV 46/40/0), `preset` (see §9), `uppercase`, `dynamic` (legacy karaoke), `highlight_color` (default #FFE14D), `animation ∈ fade/pop/slide_up/punch/blur_in/whip/flash/rise/drop`, plus the composer fields: `font` (12 bundled families), `effect ∈ chroma/chrome/glow`, `layout ∈ stack/flow`, `leading ∈ [0.5, 2.2]` (below 1.0 lines deliberately overlap), `emphasis ∈ big/huge/accent/pop/box/serif/chrome/glow/chroma/none`, `emphasis_scale ∈ [1.0, 3.0]`.

**MusicItem** — `{id, storage_key, start, end (program time — defaults to whole program), gain_db=-18 ∈ [-60, +12], duck=true, offset_s (seek into the track), fade_in_s, fade_out_s (clamped to half the span), loop (opt-in), duck_mode ∈ {smooth} | None (legacy step)}`.

**SfxItem** — `{id, storage_key ("sfx:<slug>" or generated key), at (program-time point, ≤ program_end−0.05), gain_db=-6}`. The −6 dB default is the pack's house level: the pack is normalized to −16 LUFS and sits above a −18 dB music bed.

**VolumeItem** — `{start, end (SOURCE time), gain_db ∈ [-60, +12]}`.

**Frame** — `{ratio ∈ source/16:9/9:16/1:1/4:5, mode ∈ crop/pad/pad_blur}`.

**InsertItem** — `{id, asset_key, kind ∈ video/image, at_output_s (must land on a speed-aware keep boundary within 0.02s or the write is rejected), duration_s ∈ [0.2, 600] (image default 3.0), source_start_s (video only), motion ∈ zoom_in/zoom_out/pan_left/pan_right (Ken Burns, images only)}`.

**VoiceoverItem** — `{id, asset_key, start_output_s, gain_db=0, duck_others=true}` (ducks everything else −12 dB while active).

**ZoomItem** — `{id, start, end (program time, span ≥ 0.2s), strength=0.25 ∈ [0.05, 1.5] (1.5 ≈ a 2.5× punch), mode ∈ punch/ease/push_in/pull_out, cx, cy ∈ [0,1] (zoom target; default center)}`.

**TransitionSpec** — ONE global spec applied at **every** junction: `{style ∈ dip_black/dip_white/whip_left/whip_right/zoom_punch/glitch/flash, duration_s=0.3 ∈ [0.1, 1.5]}`. All transitions are duration-preserving by construction — each animates within its own block's footage, so changing transitions never changes any timeline math. **There is deliberately no crossfade/xfade overlap model.**

**RegionItem** (censoring) — `{id, mode ∈ blur/pixelate/black, x, y, w, h (fractions of the SOURCE frame, ≥0.01), start?, end? (program-time window, both or neither)}`. The rectangle does not track motion; inserts are never censored.

**StylizeItem** — `{id, kind ∈ grain/vignette/glow/chromatic/dream_blur/vhs/flash/shake, start?, end?, intensity ∈ [0.05, 1.0]}`.

**GradeCustom** — `{exposure ∈ [-1,1], contrast ∈ [0.5,1.6], saturation ∈ [0,2], temperature ∈ [-1,1], tint ∈ [-1,1]}`; neutral axes collapse; composes AFTER the preset grade.

**Effects** — `{grade ∈ vibrant/warm/cool/bw/vintage/cinematic, zooms[], fade_in_s, fade_out_s ∈ [0.1, 10], transition, regions[], stylize[], grade_custom}`.

**OverlayItem** (PIP) — `{id, asset_key, kind ∈ video/image, start, duration_s ∈ [0.2, remaining], x, y: AnimFloat (keyframeable! default 0.5, range −0.5..1.5), scale=0.4 ∈ [0.05, 1.0] (fraction of frame width), opacity ∈ [0.05, 1.0], rotation (static degrees), source_start_s, entrance/exit ∈ fade/slide_left/slide_right/slide_up}`. **Video overlay audio never plays (v1).** Overlays render above zooms (a PIP must not scale with a punch-in) and below both text layers.

**TextItem** — `{id, text ≤200 chars, start, end (≥0.3s), template ∈ title/subtitle/lower_third/callout/big_number/quote/chapter, x, y, size_scale ∈ [0.4, 3.0], color, accent_color (default #FFE14D), font (12 families), entrance ∈ none/fade/pop/slide_up/blur_in/whip/rise/drop/typewriter, exit (same minus typewriter; "none" = instant, no animation), uppercase, box}`.

**VectorItem** — `{id, kind ∈ rectangle/ellipse/line/arrow/ring/progress, start, end (≥0.3s), x, y, width, height (frame fractions), color, opacity, stroke_color, stroke_width, rounding, background_color/value (progress), motion: TextMotion}`. Program-anchored and compiled locally as ASS vector paths.

**SpeedSpan** — `{id, start, end (SOURCE time, ≥0.2s), factor ∈ [0.25, 4.0]}`; audio is pitch-preserved via chained `atempo`; slow-mo **duplicates frames** (no optical-flow interpolation on this hardware — tools warn below 0.6×); factors within 0.01 of 1.0 rejected as no-ops; spans non-overlapping.

**Master** — `{loudness: "social" | None}` → −14 LUFS with a codec-safe −2.0 dBTP ceiling on preview AND final.

## 5.4 The keyframe primitive: AnimFloat

`AnimFloat = float | [Keyframe]` where `Keyframe = {t, v, ease?}`, `ease ∈ linear/in/out/in_out/hold` (the curve *into* that keyframe). Rules: max **24 keyframes**, strictly increasing times, times local to the element's own start and bounded by its duration, values clamped to the field's range; a single keyframe collapses to a constant. `clip_anim` trims curves when durations shrink. Easing closed forms are mirrored in Python (`anim_value`) and in the renderer's ffmpeg/ASS compilers. Overlays, designed text, and renderer-native vectors share this language for x/y/scale/rotation/opacity, so timeline trims preserve compound choreography.

---

# 6. Timeline Math: Three Clocks and the Remapping Engine

The most conceptually dense part of the product. Every timestamp in the EDL lives on one of **three clocks**, and confusing them is the #1 class of editing bug the codebase defends against:

| Clock | What it measures | Fields anchored to it |
|---|---|---|
| **SOURCE time** | the raw uploaded footage's clock | `keep`, `volume`, `speed`, explicit caption items, censor-region rectangles |
| **PRE-INSERT output time** | the timeline after cuts + speed but before inserts | `inserts[].at_output_s` (must land on a keep boundary) |
| **FINAL program time** | what the viewer actually watches | `music`, `sfx.at`, `voiceover`, `overlays`, `texts`, `zooms`, `stylize`, region windows |

`Timeline(keep, inserts, speed)` in `worker/timeline.py` provides the bidirectional mapping: `src_to_out(t)` (None inside a cut), `out_to_src(t)` (None inside an insert), `span_to_out(t0,t1)` (a span crossing cuts splits into pieces), `kept_words(words)` (transcript words whose midpoint survives, remapped to output time — this feeds caption generation), and `insert_positions()`. Segments are split into constant-rate pieces by `speed_pieces` — the single shared duration-math primitive used identically by the timeline, the renderer, and the program-duration calculator. An insert at a boundary plays **before** the segment that starts there. A subtle, regression-pinned rule: `span_to_out` resolves endpoints within the currently iterated segment rather than via `src_to_out`, because at a *contiguous* boundary (the shape `insert_media`'s mid-take split writes) first-match resolution silently drops the insert's duration — this shipped briefly in round 35 and changed legacy music-duck windows.

## 6.1 remap_program_items: the anchoring policy

Whenever the time base changes (cuts, speed, inserts), every program-time item must be re-anchored. `remap_program_items(edl, old_tl, new_tl)` applies **one shared policy** (used by both the agent's tools and the backend's user-drag ops, so both surfaces behave identically) and returns human-readable disclosure notes that get appended to tool results:

- **Content-anchored** (follows its footage; dropped if the footage is cut): zooms, stylize windows, and SFX (a point event remapped through source; dropped rather than clamped when past the end, because clamping would pile orphaned sounds on the last frame).
- **Program-anchored** (clamped to the new program length, or dropped): music, voiceover, overlays, texts, censor-region windows. Overlay keyframe curves are trimmed via `clip_anim` so stranded keyframes can't make validation reject an unrelated cut.

This is why "cut the first 30 seconds" doesn't leave the background music starting 30 seconds late, and why a punch-in zoom placed on a word stays on that word after further cuts.

There are **no gaps and no overlapping clips** in the model: the output is always the gapless concatenation of kept spans with inserts spliced at boundaries; exactly one video track plus the overlay/text layer stack above it.

---

# 7. The Agent: Loop, Context, Prompt, and the Honesty Layer

## 7.1 Turn lifecycle

A user message → an `agent_turn` job (**never auto-retried** — `MAX_ATTEMPTS_AGENT=1`, because replaying a turn would re-apply side effects; a worker death mid-turn is surfaced honestly by the reaper's "please send it again" note). `run_agent_job`:

1. Loads project, chat session, and the target message. If an original exists but no index → posts "I can't edit yet — the video hasn't finished indexing." If no original at all → runs a **canvas turn** (build from generated/uploaded media).
2. Builds a `ToolContext`: DB handle, project, index, per-turn workdir, cached proxy paths, and a battery of accumulators (versions written, renders, images/sfx/videos generated, URLs fetched, token counts, extra spend).
3. **Budget**: `credit_budget = user_balance + 3 grace credits`. There is deliberately **no flat per-turn spend ceiling** — a long code comment records that the old cap cut a real customer's 19-minute documentary off mid-edit ("spend cap hit: 43.01 >= 40.0") and was removed by decision. The real bounds: the user's balance, and a **450-second wall clock**.
4. Installs a thread-local **LLM recorder** that (a) feeds the live in-turn spend check, and (b) persists every model call (agent, honesty-regen, vision, image gen, sfx, video, concierge) to the `llm_calls` table with capped, key-redacted payloads — the raw material for the admin Model I/O inspector and for billing.

## 7.2 Context construction (what the model sees each iteration)

1. **System prompt** (§7.3), rebuilt fresh each turn.
2. **CAPABILITIES block** — auto-generated from the live tool registry, one line per enabled write tool with its parameter names, followed by: *"Nothing else exists. If the user asks for anything not listed (motion-TRACKED stickers pinned to moving objects, true crossfades, custom font files, ...), say so plainly and offer the closest listed alternative."* Because it's generated from the registry, prompt and reality cannot drift.
3. **CURRENT PROJECT STATE** — video metadata, the index summary, the current EDL version + description, the current keep list **verbatim as JSON** (first 40 spans), current captions config as JSON, EDL history line, uploaded music files, and (only when the catalogs actually shipped in this deployment) the built-in music/SFX library summary lines.
4. The last **20** chat messages (activity rows excluded, each capped at 2,000 chars).
5. The current user message (capped 4,000 chars) plus attachment notes — up to 4 attachments; images get captioned **once** by the vision model and cached; if vision is unavailable the note says *"you CANNOT see it. Say so honestly and ask the user to describe what matters."*

**The full-index-in-context strategy**: for videos ≤ 600 seconds whose assembled text fits in 40,000 chars, the context includes the **complete transcript** (every sentence with ids and timestamps) and **every shot's visual caption** plus a silence summary. This exists to kill the "it didn't bother to look" failure class — for a short video, the agent literally cannot claim ignorance of any spoken line or visible scene. Longer videos get head/tail elision plus retrieval tools.

## 7.3 The system prompt (what the agent is told)

~70 dense lines. The essentials:

- **Persona**: "You are Valmera, a professional video editor." Never guess timings — every timestamp passed to a tool must come from a tool result.
- **Craft rules**: do ONLY what was asked (never "fix" unmentioned footage); keep the LAST take when takes repeat; never cut mid-word (use word edges + `snap_to_words`); cut silences >0.7s but preserve meaningful pauses; verify repetitions are gone with `get_kept_transcript` before claiming so; censor via `look_at` → `blur_region` → preview-and-check, and be honest the rectangle doesn't track.
- **The audio model**: "four layers, never confuse them" — original footage audio (`set_volume`, source time), background music (`add_music`, −18 dB, ducked), SFX (`add_sfx`, −6 dB, program-time points, "an sfx is NOT music"), voiceover (`add_voiceover`, ducks everything). If a user asks to remove music that's baked into the single recorded track: *"you CANNOT separate music from speech — say so plainly."* (No stem separation exists.)
- **Caption doctrine**: premium presets are "your strongest visual weapon"; always pass 10–25 verbatim emphasis words with a preset; full documentation of the composer axes.
- **End-card honesty**: every export ends with a fixed ~2.5s Valmera card that is NOT in the EDL; downloads are ~2.5s longer than the reported program; never answer "remove the outro" by cutting the user's real last seconds.
- **Workflow**: read → write → *"ALWAYS finish by calling render_preview, then reply with a short summary"* (the system auto-renders if skipped) → fix and re-render on problems.
- **HONESTY — non-negotiable**: "Never state a change, a render, or a capability that this turn's TOOL RESULTS do not literally show." NO CHANGE ≠ change; REJECTED = nothing happened; never invent explanations for visual anomalies; past tense only about completed work.
- **Boundary**: "You cannot render the final full-resolution export — only the user can trigger that from the app."

**Deployment gating**: `system_prompt()` is a function that rewrites the prose per deployment — three find/replace tables strip built-in-music claims when the music catalog is empty, SFX-pack claims when it's absent, and the URL-fetching paragraph when `URL_FETCH_ENABLED=0`. The comment names the failure this prevents: "the round-22 failure shape: prose asserting a capability that has no tool."

## 7.4 The loop mechanics

There is no arbitrary iteration/tool-count ceiling. The loop runs inside a **progress window**: a stalled window stops honestly, while a window in which EDL versions, assets or previews are still landing may continue. Spend remains bounded by the user's real credit budget. The agent model is resolved from the user's plan rather than hard-coded. The first dispatch receives the relevant catalog; after a blueprint or write, broad filmstrip pixels and consumed look frames are compacted and tool schemas are stage-routed, with `expand_toolset` preserving access to every deployed domain. Each iteration checks shutdown/progress/spend → performs one model dispatch → executes every returned tool call → persists activity and evidence.

Substantial edits use a durable creative blueprint (story arc, sequence map, visual/motion/caption/music/SFX/color direction, semantic steps and acceptance criteria). Pure EDL work can be compiled through `apply_edit_recipe` into one version. Recipe operations may `save_as` the object they create and later operations address its generated id with `{"$ref":"alias"}`; already-fetched music and SFX can therefore be authored in the same transaction as picture, typography and motion. Asset search/download/generation/extraction remain separate because they have external side effects. Recipe structure and reference order are preflighted before any operation is staged.

- Invalid tool-arg JSON → the tool result becomes `"REJECTED: arguments were not valid JSON."` — a nudge, not a crash.
- **Every tool call is persisted as an `activity` chat message** (`name{args≤160} → result≤600`) — this is the live progress feed the studio renders as "N editing steps."
- The `ask_user` tool raises a control-flow exception that immediately posts the question and suspends the turn (`awaiting_user`) — the only cooperative mid-turn stop.
- If the model replies without tool calls → final-answer path: auto-render if the EDL changed without a preview, then the honesty gauntlet, then post.

## 7.5 The honesty layer (deterministic reply verification)

The drafted reply is checked against system-verified turn facts with three regex batteries plus an echo detector:

- **EDIT_CLAIM** (~70 patterns): "I've cut/trimmed/applied…", "captions are now karaoke…", "is now 9:16", speed/mastering/audio statives, bare past-participle openers ("Added a vibrant grade…"). Negations ("No color grade was applied") and offers ("I can make the captions fade in") are excluded.
- **RENDER_CLAIM**: "preview is rendered/ready/attached…"
- **DENY_CLAIM**: "nothing was changed" when the EDL did change.
- **Echo detection**: on zero-action turns, a draft ≥120 chars with ≥0.92 `SequenceMatcher` similarity to any prior assistant reply is flagged as re-describing old work.

On violation: **one forced regeneration**, with the draft plus a system message containing the turn facts (EDL vN→vM, which write tools succeeded, "a generated image is IN the video only if an insert_media write also succeeded", preview state) and the exact quoted fabrications. If the redraft still violates: real work happened → publish with a corrective prefix note; **zero-write fabrication** → both drafts discarded (kept in the job result for admin inspection) and a system-authored fallback posted: *"I wasn't able to make that change — it needs a capability I don't have yet; nothing was modified this turn,"* augmented with the nearest real capability from `ALTERNATIVE_HINTS` (an ordered regex table mapping the user's ask to the closest existing tool, each hint gated on deployment reality).

**Structural weakness worth knowing**: the entire battery is English regex. A non-English reply bypasses it, and the pattern list is inherently a whack-a-mole (the file documents several rounds of false-positive patching).

## 7.6 Billing the turn

After the handler returns, `charge_turn_credits` sums the job's `llm_calls` rows — tokens at $2/$6 per M, $0.07 per successful image, real `cost_usd` on sfx/video rows — converts at 1 credit = $0.01, floors at 1 credit, and deducts daily → bonus → monthly under a row lock, with the ledger insert in a SAVEPOINT so a ledger hiccup can't roll back the charge. Billing failure never fails a finished edit.

## 7.7 The concierge (pre-index chat)

Before the index exists, messages don't start agent turns. Instead a **concierge** LLM call (same model, 14s timeout, 300 tokens) runs in a daemon thread (the backend has only 3 sync gunicorn workers, so blocking one for 14s is unacceptable). Its system prompt carries an exhaustive, provider-gated capability list mirroring the worker's tool gates so the two surfaces can't disagree. In the no-video stage it returns forced JSON `{act, reply}`, and `act=true` (a real "make me a video" request) enqueues a blank-canvas agent turn under an advisory lock. It has its own honesty regex (`_CONCIERGE_CLAIM`) discarding drafts that claim past-tense work. Concierge calls are logged to `llm_calls` (purpose `concierge`) and **never charged**.


---

# 8. The Complete Tool Catalog

Everything the agent can do, exhaustively. The registry (`agent_tools.py`) maps each name to (function, description, param schema); 47 names are classified as **write tools**; disabled tools (no image model, empty catalogs, fetch disabled) are hidden from the model's schema AND scrubbed from other tools' descriptions. Every write funnels through validation; results follow the strict protocol `"EDL vN -> vN+1: <diff>"` / `"NO CHANGE — …"` / `"REJECTED (EDL vN unchanged): <reason>"`. Tool outputs are capped at 12,000 chars (transcripts 48,000; get_edl 20,500 — raised from 8,000 because the old cap "silently amputated exactly the collections a v2 edit needs to see").

## Read / perception tools

| Tool | What it does |
|---|---|
| `get_video_info()` | Metadata + counts (shots, sentences, words, silences ≥0.7s) + current EDL summary. Canvas projects get canvas guidance instead. |
| `get_transcript(start, end)` | Sentence-level source transcript (48k char budget). |
| `get_kept_transcript()` | The program-time transcript of what the current edit KEEPS, with source spans per line, plus **repeated-phrase detection** (4-word shingles) reporting "POSSIBLE REPETITIONS still in the output." |
| `get_words(start, end)` | Word-level timestamps, ≤400 words per response with paging instructions. |
| `search_transcript(query)` | Substring (≤20 hits) + difflib fuzzy (≤8) sentence search. |
| `get_shots(start, end)` | Shots with their vision captions (setting/people/action/on-screen text). |
| `find_silences(min_seconds=0.7)` | ≤100 silences with midpoints and surrounding words. |
| `list_assets(kind)` | music/image/clip/render/all. The pipeline's own extracted `audio` artifact is deliberately excluded everywhere (root cause of an old inaudible-music bug). |
| `look_at(start, end, question)` | Vision Q&A over 2/4/6 evenly-sampled proxy frames; letterbox context included so bars don't read as broken frames. |
| `look_at_asset(asset_key, question, start, end)` | Same for uploaded clips/images — "THE way to pick which moment of a long clip to splice." |
| `get_audio_analysis(asset_key?)` | Tempo + confidence verdict, beat grid, energy peaks/quiets/biggest rise, top-8 stressed words. Works on the main video, an uploaded music file, or a library track ("find the drop for add_music offset_s"). |
| `get_edl()` | Full current EDL JSON. |
| `suggest_emphasis()` | ≤25 candidate emphasis words: measured vocal stress + digit words + rare distinctive words, verbatim. |

## Cutting tools (all funnel through a shared keep-list writer)

The shared tail does: optional outward word-snapping, drops sub-0.05s slivers, re-snaps inserts to new boundaries, **re-anchors every program-time item** via `remap_program_items`, then appends mid-word-boundary WARNINGs, regression warnings (a full keep replacement that re-includes previously-cut material gets annotated "mostly silence" / "verbatim duplicate of sentence X"), and a **large-drop warning** when a write removes >50% of kept footage ("If the user did not EXPLICITLY ask to shorten the video this much, put the footage back…").

| Tool | Behavior |
|---|---|
| `keep_segments(segments, snap_to_words)` | Replaces the whole keep list (wholesale restructuring only; regression-checked). |
| `cut_range(start, end, snap_to_words)` | Subtract one span. |
| `restore_range(start, end, snap_to_words)` | Union a span back (non-destructive undo of any cut). |
| `cut_silences(min_silence_s=0.5, padding_s=0.12)` | One call cuts every detected silence, keeps padding around speech, always word-snapped. |
| `remove_filler_words(words=None)` | Cuts default fillers (`um, umm, ummm, uh, uhh, uhm, er, err, erm, ah, ahh` — deliberately excluding "like"/"you know"/back-channel affirmations); custom lists support multi-word phrases; reports per-token hit counts. |

## Caption tools

- `add_captions(mode, items, style, max_words_per_caption, emphasis_words)` — `from_transcript` (default), `off`, or manual items. Carries a three-tier honesty gate counting transcript words that actually survive the cut: zero visible → "captions will show NO text at all — tell the user honestly"; <5 → "sparse" note.
- `set_caption_style(style, emphasis_words)` — partial patch; unknown fields rejected **by name** with a full field manual (closing the silent-drop bug class).

## Audio tools

- `list_music_library(mood)` / `add_music(storage_key, start, end, gain_db=-18, duck=true, offset_s, fade_in_s=1.0, fade_out_s=2.0, loop=true)` / `swap_music(id, storage_key)` / `set_music_fit(id, …)` / `remove_music(id)`. Resolution accepts ONLY `library:` catalog refs or the project's own music assets — specifically rejecting the extracted-speech artifact ("mixing it in would only double the speaker's voice") and warning when a file is active as both music and voiceover ("it will play TWICE").
- `list_sfx_library(category)` / `add_sfx(storage_key, at, gain_db=-6)` / `move_sfx(id, at)` / `remove_sfx(id)`. The SFX resolver is "a structural twin of the music resolver, deliberately just as strict… a loose check here is a read primitive over the whole bucket."
- `add_voiceover(asset_key, start_output_s=0, gain_db=0, duck_others=true)` / `remove_voiceover(id)`.
- `set_volume(start, end, gain_db)` — source-time automation on the speaker track. `set_audio_gain(kind, id, gain_db)` — retune an existing music/sfx/voiceover item ("NEVER set_volume" for those).
- `set_master_loudness(enabled)` — −14 LUFS / −2.0 dBTP codec-safe mastering toggle.

## Visual tools

- `set_frame(ratio, mode)` — source/16:9/9:16/1:1/4:5 × crop/pad/pad_blur.
- `set_color_grade(preset)` — vibrant/warm/cool/bw/vintage/cinematic/none. `set_grade_custom(exposure, contrast, saturation, temperature, tint)` — axis-by-axis merge composing after the preset.
- `add_zoom(start, end, strength=0.25, mode, cx, cy)` / `remove_zoom(id)`.
- `set_fades(fade_in_s, fade_out_s)`; `set_transitions(style, duration_s=0.3)` (applies at EVERY junction; description includes one honest line per style; "True crossfades still do not exist — say so when asked").
- `blur_region(x, y, w, h, mode, start, end)` / `remove_blur(id?)` — censoring; result appends "render_preview and CHECK the sheet."
- `add_stylize(kind, start, end, intensity)` / `remove_stylize(id)` — grain/vignette/glow/chromatic/dream_blur/vhs/flash/shake. Prompt guidance: "one or two read as a look, five read as a broken TV."
- `set_speed(start, end, factor)` / `remove_speed(id)` — 0.25–4×, source-time (survives later cuts), pitch-preserved; warns below 0.6× about frame-duplication stepping; reports old→new program length.

## Composition tools

- `insert_media(asset_key, at_output_s, duration_s, clip_start_s, motion)` — splices a clip/image INTO the timeline. Clips >15s **require** an explicit window (chosen via `look_at_asset`). Placement: snap to a keep boundary within 0.25s, else **split the containing take at the nearest word edge**. On canvas programs the first asset placed fixes the canvas aspect. `remove_insert(id)` restores timing with full re-anchoring.
- `add_overlay(asset_key, start, duration_s, x, y, scale=0.4, opacity, entrance, exit, source_start_s)` / `move_overlay` / `remove_overlay` — PIP/logo layer; x/y accept **keyframe lists** for drifts; always appends the honest-limits note (overlay audio does NOT play; renders below captions; never tracks objects).
- `add_text(text, start, end, template, x, y, size_scale, color, accent_color, font, entrance, exit, uppercase, box)` / `remove_text(id)` — the motion-graphics layer (§10).
- `add_vector_graphic(kind, start, end, geometry, palette, motion)` / `set_vector_graphic` / `remove_vector_graphic` — general panels, connectors, arrows, rings, and progress indicators with the same local keyframe language as text; no inference or fetched asset.

## Director tools (perception-driven, deterministic)

- `punch_in_on_emphasis(count=4, strength=0.35)` — punch zooms on the most vocally stressed **surviving** words, ≥4s apart, 0.9s windows starting 60ms before the word attack.
- `beat_align_cuts(tolerance_s=0.35)` — moves internal cut boundaries onto the beat grid; **refuses below BPM confidence 0.5** ("'Syncing' cuts to a pulse that isn't really there would be a lie"); skips moves that land inside words or collide with neighbors.
- `sound_design_pass(intensity=light/medium/strong → 2/4/6 placements)` — one impact on THE strongest stressed word, one riser resolving into the biggest energy rise, whooshes on junctions ≥5s apart, never within 1.5s of existing sounds; deterministic picks (alphabetically first per category) so re-runs are NO CHANGE, not reshuffles.
- `apply_look(name ∈ hype/clean/cinematic/luxury/meme)` — one-call composition of caption preset + grade + custom grade + transitions + fades + stylize as plain EDL data (e.g. hype = beast XL captions + vibrant + zoom_punch + 0.6s fade-out). Never touches cuts/music/sfx.

## Generative tools (§13) and meta

- `generate_image`, `generate_sfx`, `generate_video`, `fetch_url` — all budget-pre-checked, all hammering "the asset is NOT in the video until you insert_media / add_music it."
- `render_preview()` — enqueues a preview job and polls synchronously (up to 900s). Deduped per version per turn. The result carries: cached-vs-fresh distinction, the **vision self-check** over a 3×3 contact sheet ("does anything look broken… If it looks fine, say 'looks clean'"), the **mid-word audit**, the **caption audit** (captions ON but zero words survive), and the **repetition audit**.
- `ask_user(question)` — suspends the turn for a taste call. "ONCE, for taste calls only."

---

# 9. Captions: The Full System

Captions are Valmera's deepest visual feature. Everything is compiled into **ASS subtitle files burned by ffmpeg's `subtitles` filter via libass** — no PIL frames, no drawtext, no PNG compositing. Two independent files per render: `captions.ass` (the caption layer) and `graphics.ass` (the text layer, burned second — "a title always wins over a caption crossing it"). Both are **deterministic and byte-stable**: same EDL + frame → byte-identical file, pinned by regression tests, because render caching and version history depend on it.

## 9.1 The pipeline

1. **Words in**: `tl.kept_words(index.words)` — only transcript words surviving the current cut, already remapped to program time. Timing is never invented.
2. **Grouping** (classic path): accumulate words; flush when chars exceed the frame-aware line budget (default 42 chars/line × 2 lines), when `max_words_per_caption` is reached, or on a **speech gap > 1.2s**. Event end = max(last word end, start + 0.6s). Line-wrap is frame-aware: real chars-per-line = `min(42, usable_width / (0.52 × font_px))` so libass never re-wraps a 2-line chunk into 4 on a 9:16 frame.
3. **Premium path adds**: sentence-final punctuation splits, per-preset words-per-line, and the whole treatment/effect engine below.
4. **First-frame lead-in** (`FIRST_CAPTION_LEAD_IN_S=2.0`): mobile players don't autoplay, so a paused player shows frame zero. If the first caption starts within 2s, its start snaps to 0.0 so the opening frame carries text — a fix for repeated real reports of "captions didn't apply." Exceptions: genuinely silent intros >2s, dictated items, and programs opened by an inserted clip.
5. **Overlap discipline**: an event's end clamps to the next event's start; in karaoke modes, events whose successor starts within 10ms are dropped outright (a clamped sliver would still render one stacked frame — same-layer overlaps make libass stack two copies).

Scaling convention: everything is authored against a **1280×720 reference frame**; `PlayResX/Y` is set to the real output frame and font sizes scale with `max(fx, fy)` — the larger factor — so 9:16 verticals get properly big text (width-only scaling had left vertical-video text "unreadably small").

## 9.2 The style axes

- Sizes s/m/l/xl → 30/40/52/68 px (at 720p reference) × continuous `size_scale` 0.5–3.0.
- Positions bottom/top/middle → ASS alignments 2/8/5, vertical margins 46/40/0 (scaled by frame height).
- Legacy default font: **DejaVu Sans** (system). `style.font` is honored even without a preset — the only path to Montserrat, and an explicit honesty fix ("silent font-drop" gap).
- Static entrance animations: `fade` → `\fad(160,120)`; `pop` → 70%→106%→100% scale ramp; `slide_up` → `\move` from below + fade. Full enum: fade/pop/slide_up/punch/blur_in/whip/flash/rise/drop.
- Legacy karaoke (`dynamic: true`): one Dialogue per word; the active word renders at 62% scale, overshoots to 114% by 90ms, settles at 106% by 170ms, in the highlight color (default #FFE14D).

## 9.3 The 11 premium presets (exact parameters)

Three modes: **reveal** (words appear as spoken and stay), **karaoke** (whole group visible, spoken word lights up), **static** (whole phrase with an entrance). Two layout engines: **flow** (single Dialogue per state; the byte-frozen original four) and **stack** (one Dialogue per line with its own `\pos`, enabling overlapping leading <1.0, per-line stagger, and layered effects).

| Preset | Font | Base px | Mode | Upper | Words | Layout | Word anim | Active style | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `podcast` | Inter Display ExtraBold | 44 | reveal | no | 5 | flow, left, middle | pop | — | treatments rotate accent/box/serif |
| `beast` | Anton | 54 | karaoke | YES | 3 | flow, center | — | accent | the MrBeast look; outline 3.0 |
| `karaoke` | Inter Display ExtraBold | 46 | karaoke | no | 3 | flow, bottom | — | box (traveling accent box) | |
| `elegant` | Inter Display Bold | 38 | static | no | 8 | flow, bottom, fade-in | — | — | serif/accent emphasis |
| `stacked` | Inter Display Black | 46 | reveal | no | 4 | stack, leading 0.86, stagger 0.055 | punch | — | emphasis scale 2.05 |
| `iridescent` | Inter Display Black | 44 | reveal | no | 4 | stack, leading 0.84 | blur_in | — | chroma (RGB-split) emphasis |
| `chrome` | Inter Display Black | 48 | reveal | no | 4 | stack, leading 0.88 | punch | — | 11-band metallic chrome ramp |
| `editorial` | Instrument Serif | 46 | reveal | no | 5 | stack, leading 1.06 | fade | — | |
| `fashion` | Archivo Black | 40 | reveal | YES | 4 | stack, leading 0.98, stagger 0.04 | rise | — | |
| `luxe` | Playfair Display Black | 44 | reveal | no | 5 | stack, leading 1.0 | fade | — | |
| `impact` | Bebas Neue | 58 | karaoke | YES | 4 | stack, leading 0.9 | punch | accent | |

Plus `classic` (explicit legacy). Preset sizes multiply by the size setting (s 0.8 / m 1.0 / l 1.3 / xl 1.6); max 3 lines.

## 9.4 Emphasis treatments and layered effects

**Treatments** (orthogonal per-word properties, assigned by rotating the preset's treatment tuple across the agent's `emphasis_words`, carried across chunks so the look varies): `big` (size only — "ONE WHITE WORD at ~2× its white neighbours"), `huge`, `accent` (color), `pop` (size+color), `box` (filled accent box behind dark text — at most one box per chunk), `serif` (DM Serif Display italic + accent + size bump), `chrome`, `glow`, `chroma`. Words containing **digits are always emphasized** (first digit word per chunk gets the huge treatment). In karaoke modes, keyword emphasis is stripped down so persistent coloring never buries the spoken-word highlight.

**Layered effects** — libass can't gradient-fill or blur-shadow a glyph, so effects are extra copies of the same text run on different ASS layers:

- **chroma**: two under-copies offset ±3% horizontally, one pure red, one cyan, at reduced alpha — an RGB-split fringe.
- **glow**: one under-copy with `\blur` at ~9% of font size in the accent color.
- **chrome**: a genuinely clever hack — **11 horizontal grey bands**, each a full copy of the line `\clip`ped to its horizontal slice, with a dark "horizon" ~40% down and a specular bounce below it ("what makes it read as polished metal"), over a dark backing copy for shadow separation.
- The masking trick that makes per-word effects possible: the whole line is redrawn with non-target words at `\alpha&HFF&` (invisible), so an effect applies to ONE word without disturbing line spacing. A pinned bug: alpha override tags persist across segments in libass, so `\alpha&H00&` must be restated explicitly or every word after the first masked one goes invisible.

**Word entrance animations**: pop (62→108→100%), punch (44→113→100%), fade, blur_in, whip (14° rotation in), flash (white flash), rise/drop (line-level `\move`, since `\move` can't coexist with the composer's `\pos`).

## 9.5 Fonts (15 TTFs bundled in `worker/fonts/`, loaded via `fontsdir`, never installed system-wide)

Inter Display Black / ExtraBold / Bold (+BoldItalic), Anton, Bebas Neue, Archivo Black, Playfair Display Black (+Italic), Instrument Serif (+Italic), DM Serif Display (+Italic), Poppins Black, Syne ExtraBold, Montserrat Bold — with license files for all. Poppins/Syne/Montserrat are override-only (no preset uses them). Unicode/RTL/emoji handling is entirely delegated to libass + the Docker image's Noto/Noto-CJK fallback fonts; no explicit BiDi logic exists.

## 9.6 Hard-won implementation rules

- A composer field must exist simultaneously in `CaptionStyle` (schema), `STYLE_KEYS` (captions.py), and the tool allowlist — or it is **silently dropped while the agent reports success**. This bug class is documented at three separate sites.
- Byte-stability handcuffs: the four flow presets must emit byte-identical ASS forever; new behavior rides new fields.
- All text wrapping is heuristic (per-family glyph-width fractions 0.40–0.62 × char count), not measured shaping — absorbed by margins, the stack-mode overflow clamp (an over-wide word is silently rendered smaller), and graphics' shrink-to-fit.

---

# 10. The Text & Motion-Graphics Layer

`EDL.texts` and `EDL.vectors` form a separate motion-graphics system (`graphics.py` → `graphics.ass`, burned above captions). Both are PROGRAM-anchored and deliberately decoupled from transcript captions ("adding a title card must never invalidate a caption cache — two files, two burns, zero coupling"). The compiler is deterministic: it reads no index or clock, and byte-equality is unit-tested.

## 10.1 The 7 templates

| Template | Font | Base px | Anchor | Style notes |
|---|---|---|---|---|
| `title` | Inter Display Black | 66 | center, y 0.42 | uppercase, rise-in/fade-out |
| `subtitle` | Inter Display Bold | 30 | center, y 0.60 | uppercase, wide letter-spacing 3.2 |
| `lower_third` | Inter Display ExtraBold | 38 | left, x 0.085, y 0.84 | slide_up-in; two-deck ("Name | Role") |
| `callout` | Inter Display ExtraBold | 38 | center, y 0.28 | **accent-filled box** behind dark text, pop in/out |
| `big_number` | Anton | 118 | center, y 0.44 | two-deck ("42 | DAYS LEFT"), pop-in |
| `quote` | DM Serif Display italic | 44 | center, y 0.40 | accent “ ” marks auto-added; two-deck attribution |
| `chapter` | Bebas Neue | 52 | center, y 0.14 | letter-spacing 5.0, whip-in |

Two-deck splitting: on deck-aware templates, the first `\n` or `" | "` separates the main line from a smaller accent-colored secondary deck.

## 10.2 Mechanics worth knowing

- Time floor 0.3s; items are clamped into the program, never truncated textually ("dropping the user's words would be a silent lie") — oversized blocks **shrink-to-fit** instead (fonts scale down uniformly, floors 10/8px).
- Anchors clamp fully on-frame even at x=1.0/y=0.0 (unit-tested).
- Entrances (~220ms): pop/blur_in/whip/fade + move-anims (slide_up/rise/drop) via `\move`. Exits mirror in a 0.25–0.4s tail window. `\move` is single-occupancy: a moving entrance + moving exit keeps the entrance and degrades the exit to fade (tested, never dropped).
- **Typewriter** entrance: per-glyph alpha reveal windows, cadence continuous across lines and decks, capped at **40 animated glyphs** (~40 bytes of override tags each; text past the cap appears with the last window); forces exit=fade (the one exit that provably composites with per-glyph alpha).
- `EDL.vectors` adds rectangle/panel, ellipse, line, arrow, ring, and truthful progress primitives. Geometry uses frame fractions; color, stroke, rounding and opacity are authored data; general local x/y/scale/rotation/opacity curves provide compound animation. They compile to native ASS `\p1` paths—no generated/fetched asset, no model call, and no extra render input. Images/stickers remain media overlays rather than vector graphics.

---

# 11. Effects, Transitions, Zooms, Speed & Grades

The complete visual-effects inventory with its exact ffmpeg mapping. Filter order after concat: grade preset → grade_custom → stylize → zooms → overlays → caption burn → graphics burn → fades → end card.

## 11.1 Color grades

| Preset | ffmpeg |
|---|---|
| vibrant | `eq=saturation=1.35:contrast=1.08` |
| warm | `colorbalance=rs=.08:gs=.02:bs=-.08, eq=saturation=1.12` |
| cool | `colorbalance=rs=-.05:bs=.08, eq=saturation=1.05` |
| bw | `hue=s=0, eq=contrast=1.1` |
| vintage | `curves=preset=vintage, eq=saturation=0.85` |
| cinematic | `colorbalance=bs=.05:rs=-.03, eq=contrast=1.12:saturation=1.12:brightness=-0.02` |

Custom grade: exposure → eq brightness (±0.35), contrast/saturation → eq, temperature/tint → colorbalance on shadows+mids (chosen over `colortemperature` for cross-ffmpeg-version stability).

## 11.2 Stylize kinds (intensity-parameterized, optionally windowed via `enable=between(t,a,b)`)

grain (`noise`), vignette, chromatic (`rgbashift`), dream_blur (`gblur`), vhs (rgbashift + noise + desaturate), flash (brightness), glow (split → gblur → screen-blend), shake (windowed `zoompan` with sine/cos wobble at ~11–21 Hz, zoom 1 outside the window so no hidden crop).

## 11.3 Zooms

All zooms compile into ONE `zoompan` filter whose `z` expression sums per-zoom terms (this forces CFR normalization): `punch` = instant step; `ease` = ramped in/out (ramp = span/4 clamped 0.15–0.4s); `push_in` = Ken Burns 0→strength; `pull_out` = strength→0. Targets default center; `cx/cy` compose per-zoom pan expressions.

## 11.4 Transitions (all duration-preserving; per-junction window = min(duration, block/2 − 0.05))

- `dip_black` / `dip_white`: per-block fades to/from color at block edges.
- `flash`: additive white brightness ramp peaking exactly on the cut.
- `glitch`: rgbashift + noise enabled in the junction window.
- `whip_left/right`: a post-concat composite — black backdrop + the program overlaid with a quadratically accelerating horizontal throw into each cut, plus directional blur (`dblur`) in the junction windows. Emitted post-concat as a single instance to keep the graph small on the 1-vCPU box.
- `zoom_punch`: one post-concat zoompan — pushes 45% into the cut, lands from a 30% over-zoom; half-open windows so the junction frame belongs to the incoming side.

## 11.5 Speed and Ken Burns

Speed spans split each keep segment into constant-rate pieces: video `setpts/factor`, audio chained `atempo` (each instance legal only 0.5–2.0, so 3× = `atempo=2.0,atempo=1.5`). Image-insert `motion` compiles per-block zoompan: zoom_in `1→1.25`, zoom_out `1.25→1`, pan_left/right full-width pans at 1.15× zoom.

## 11.6 Censoring

Regions burn into each SOURCE segment **before** reframe (the coordinates the agent chose from `look_at` frames stay valid): black = `drawbox`, pixelate = crop → downscale (factor = min(w,h)/8) → neighbor-upscale → overlay, blur = crop → `gblur` (sigma clamped 3–30; gblur, not boxblur, because boxblur's chroma-radius constraint breaks on small yuv420p regions) → overlay. Windowed regions are remapped program→segment-local time, speed-aware.


---

# 12. Audio: Four Layers, Music Library, SFX, Ducking, Mastering

## 12.1 The four-layer model

The product's audio architecture, as taught to the agent verbatim ("four layers, never confuse them"):

1. **Original footage audio** — the speaker track. Gain automation via `set_volume` in SOURCE time. Silence-triggered ducking of music is computed against its speech spans.
2. **Background music** — `music[]` items. Default −18 dB, auto-ducked under speech.
3. **SFX** — `sfx[]` point events. Default −6 dB. Never loops, never ducks ("an accent that dips under the very word it is punctuating is not an accent").
4. **Voiceover** — `voiceover[]` items at 0 dB, ducking everything else −12 dB while active.

The final mix: `amix=inputs=N:duration=first:normalize=0`. Ordinary unmastered mixes deliberately add no limiter; headroom comes from pack normalization (−16 LUFS) plus conservative defaults. Optional mastering (`master.loudness="social"`) applies `loudnorm=I=-14:TP=-2.0:LRA=11` followed by a latency-compensated hard ceiling (`alimiter ... latency=1`) to the program only, before the end-card concat (so the card's silence never drags the integrated measurement). This guards AAC/inter-sample overs without shifting audio against picture. Everything returns to 48 kHz stereo fltp.

**Ducking, two generations**: legacy items use a hard −12 dB `volume` step enabled over transcript speech spans (merged until ≤80 enable-expressions); new music items get `duck_mode: "smooth"` — `sidechaincompress=threshold=0.03:ratio=12:attack=180:release=550` keyed off the program feed (threshold ≈ −30 dBFS: real speech, not room tone), so the bed breathes with the voice.

**Music plumbing details**: `offset_s` + loop are expressed as `-stream_loop -1` plus a single `atrim` (never `aloop`, which buffers the whole track in RAM — "this worker has OOM-crashed before"); fades are applied while t=0 is still the track's first sample, then `adelay` positions it; a 0.25s tail-fade is added before the end card when the user has no explicit fade-out so music never cuts dead into card silence.

## 12.2 The built-in music library (24 tracks, all CC0, bundled in the Docker image)

Both libraries are instances of one `Library` class with a security-relevant property: resolution is a **whitelist lookup** (`library:<slug>` must exactly match the catalog; the filesystem path is built from the catalog entry's filename, never the caller's string) — because the renderer's fetcher has no project scoping, this lookup is the only thing preventing an EDL from naming another customer's object. Missing files are silently dropped from the catalog at load, so tools honestly report nothing available rather than promising phantom tracks.

Moods (deliberately plain-language): `upbeat, chill, cinematic, corporate, dramatic, hiphop, ambient, inspiring` — 3 tracks each. Authors: HoliznaCC0 (13), Soundtrack 4 Life (6), TRG Banks (3), Ondrosik (2); all from Free Music Archive, license verified twice per track against machine-readable CC0 markers, pulled from FMA's public stream endpoint, loudness-normalized to ~−16 LUFS and silence-trimmed so `gain_db` means the same on every track. Track durations 103–180s. BPM is `null` in the manifest for all 24 (FMA doesn't publish it), so **the agent never sees a tempo when browsing**.

The fascinating dead asset: `features.json` — 3,765 lines of rigorous offline measurement per track (BPM with confidence and octave-ambiguity flags, spectral centroid, dynamic range, 8-segment energy arcs, low-end fractions, stereo metrics, vocal-presence probes, deterministic tags and `not_for` anti-tags, a six-criterion "epic trailer bar" that **zero of 24 tracks pass**) — and **nothing at runtime loads it**. Music selection today is mood-label + title only. The measured conclusions worth knowing: 17/24 tracks are octave-ambiguous in tempo; vocal presence is `unknown` on all 24 (both probes inconclusive — never promise "guaranteed instrumental"); and "make it epic" cannot be honestly served from this library.

## 12.3 The built-in SFX pack (18 sounds, all synthesized in-house, CC0)

Every sound was **generated by numpy DSP code** (`tools/build_sfx.py` over `tools/dsp.py`: band-passed noise sweeps with integrated phase, pitch-collapsing impacts with grit and FFT-convolution reverb, additive bells with inharmonic partials, mid/side stereo widening), because shipping third-party audio inside customers' monetized exports carries license obligations the product can't discharge — synthesized sounds are owned outright. The pack is loudness-matched to **−16 LUFS momentary max** with a −1 dBFS peak ceiling (peak-normalizing alone had left a 17.8 dB perceived-loudness spread), and every sound is verified by *measurement* (spectral-centroid trajectory must match the claimed shape — "I cannot listen to these").

| Category | Sounds |
|---|---|
| ui | click (0.055s), tick (0.042s), pop (0.14s), shutter (0.24s) |
| transition | whoosh (0.72s), whoosh-deep (1.05s), swipe (0.30s), whoosh-reverse (0.55s), glitch (0.45s) |
| impact | impact (1.3s), impact-soft (0.85s), boom (2.4s), sub-drop (1.3s), zap (0.32s) |
| riser | riser (2.6s) |
| alert | ding (1.3s), chime-up (1.0s), buzz (0.5s) |

Prompt-taught placement grammar: whoosh/swipe ON the cut, impact/boom on the reveal or strongest word, riser starting ~2s before a cut so it *resolves* there, 3–6 well-placed accents beat one per cut.

## 12.4 What audio Valmera cannot do

No stem separation (baked-in music cannot be removed from a recorded track — the agent is told to say so plainly), no denoise/restoration, no per-speaker balancing (no diarization), no TTS, no beat-matched music retiming, and overlay-video audio never plays.

---

# 13. Generative Tools: Images, Video, Sound, URL Fetching

All four share the same economics discipline: **pre-check the credit budget before spending real money** (`_gen_budget_reject`), log every attempt (including failures) to `llm_calls` with exact model + error, bill only when the thing actually succeeded, and repeat in every result string that a generated asset **"is NOT in the video yet"** until an `insert_media`/`add_music` write lands (the round-26 lesson: users were told images were "added" when they were merely generated).

## 13.1 Images — `generate_image(prompt, from_video_time_s?, from_asset_key?, aspect)`

Dual backend auto-detected from the endpoint: **openai-compatible** (current: xAI `/images/generations`, model `grok-2-image-1212`) is **text-to-image only**; **DashScope native** additionally supports restyle-a-frame and restyle-an-image via `IMAGE_EDIT_MODEL` (qwen-image family, with per-model size tables and an automatic size-rejection retry). On xAI, the restyle modes are hard-rejected honestly and the agent falls back to fresh generation. $0.07/image (~7 credits), max 8/turn, results uploaded to `generated/<project>/<hex>.png` and registered as `image_ref` assets with caption + model metadata. Historical scar: the bare model id `grok-2-image` 404'd on every call and **silently killed image gen for a week** until July 22, 2026 — which is why every attempt now logs the exact model and error to the admin Model I/O tab.

## 13.2 Video — `generate_video(prompt, from_image_asset_key?, duration_s≈5)`

Provider: **fal.ai** queue API (one key, many models switchable by env); default **Kling 2.5 Turbo Pro image-to-video**. Kling durations snap to exactly 5 or 10 seconds. Submit → poll (6s interval, 240s timeout) → download → re-host to R2 (fal URLs are temporary). The poll budget is deliberately sized so submit(30) + poll(240) + response(30) + download(90) ≈ 390s fits inside the 450s turn wall — a fragile but documented arithmetic contract. Pricing $0.35 base (first 5s) + $0.07/s (~35 credits for 5s, ~70 for 10s), max 3/turn. Registered as a `video_clip` asset — again, not in the program until spliced.

## 13.3 Sound — `generate_sfx(prompt, at, duration_s 0.5–22, gain_db=-6)`

ElevenLabs sound-generation, `prompt_influence 0.3`. Placement is validated BEFORE spending money (a paid sound is never orphaned); generation, upload to `generated_sfx/…`, and EDL placement happen in the same call; the flat $0.08 is billed only if the write succeeded. Max 10/turn. The agent is told to prefer the free built-in pack first.

## 13.4 URL fetching — `fetch_url(url, as_kind?)`

The most security-sensitive tool. Two paths: **direct files** (extension or Content-Type sniff via a ranged GET) and **pages** (YouTube/TikTok/Vimeo/SoundCloud via **yt-dlp as a subprocess** — never the Python API, so a hung extractor dies with the 180s timeout and its process group, including the merge ffmpeg, is SIGKILLed). Direct failures fall back to the extractor and vice versa. **Classification is by ffprobe, never by extension or Content-Type** — including the genuinely hard case of an MP3 with embedded cover art (ffprobe reports a "video" stream; `attached_pic` disposition is treated as authoritative), and the gif-vs-gif_pipe distinction that once filed every still GIF as a 0.04s video clip.

yt-dlp hardening: `--ignore-config` (a user-level config could inject `--exec`), `--no-playlist`, `--match-filter !is_live`, `--max-filesize`, format capped at 1080p ("4K is a ~10× download for a 1080p timeline"), music preference extracts audio to mp3. Error surfacing scrubs anything address-shaped from messages — otherwise attacker-chosen URL + readable error = "a port scanner with an oracle."

The SSRF sandbox (`net_fetch.py`) is three-layered: (1) resolve-first, all-or-nothing — every DNS answer must be a public address (RFC1918/loopback/link-local/metadata-service all refused); (2) **post-connect peer verification** via `getpeername()` — closing the DNS-rebinding window between our resolution and requests' own; (3) every redirect hop re-validated (max 4). Plus a real **absolute deadline**: requests' timeout is per-socket-read and resets on every byte — a 1-byte-per-3s dripper holds a connection forever — so a timer thread calls `sock.shutdown()` at the deadline ("closing the socket from another thread is the only thing that interrupts a blocked recv"), spanning the whole redirect chain. Fetched files land under `fetched/{project}/…` with per-kind size caps matching upload limits "so a pasted link and a drag-and-drop behave identically." Each fetch attempt gets a **fresh workdir** — a reused dir once let a failed fetch's leftovers be registered as the next link's media, "the one failure the honesty layer cannot see."

Acknowledged residual risk, verbatim from the docstring: yt-dlp does its own networking, so address checks cover only the user's URL, not extractor-derived CDN URLs. "That is mitigation, not elimination. The real fix is egress policy on the worker, which does not exist today."

---

# 14. Rendering & Encoding: How Pixels Actually Get Made

## 14.1 The one-process design

Every render — preview or final — is **one ffmpeg invocation with one `-filter_complex` graph**. No intermediate renders, no per-segment files, no stream copying (always re-encode). Inputs: the main source (proxy for previews, ORIGINAL for finals), one shared `anullsrc` silence generator when needed, then one input per music/sfx/insert/voiceover/overlay item, and finally the end-card PNG.

The graph, in order: source-time volume automation (applied before trimming so windows stay in authored source seconds) → censor regions (burned into source segments before reframe) → per-segment trims + speed pieces → normalization (see below) → insert blocks (with Ken Burns) → per-block transitions → concat → grade → grade_custom → stylize → zoompan (all zooms + shake + zoom_punch) → overlays → `subtitles=captions.ass` → `subtitles=graphics.ass` → fades → preview downscale (previews only) → end-card concat. Audio: per-block atrim/atempo → concat → music chains (fade/volume/aresample/adelay/duck) → sfx chains → voiceover → amix → program fades → optional loudnorm → end-card silence concat.

**Cheap vs. normalized path**: a plain cut of a single source runs the legacy cheap graph — per-segment trim/setpts with no scaling, preserving source resolution/fps/SAR exactly. Normalization of every block to exact W×H @ CFR yuv420p is forced only when needed: inserts, frame changes, zooms, speed, overlays, whip/zoom_punch transitions, or shake. Normalization modes: crop (scale-to-cover + crop), pad (fit + black bars), pad_blur (blurred cover behind a fitted foreground). `frame_dims()` computes output geometry without ever exceeding the source's pixel budget. fps caps at 60 on the normalized path; the cheap path preserves source fps (a 120fps export keeps its rate).

A subtle correctness detail: if the container duration exceeds the picture track by >max(0.4s, 2%) — iOS screen recordings do this — the last frame is held with `tpad=stop_mode=clone`, matching what players (and the proxy) show.

## 14.2 Encode settings

| Output | Source | Video | Audio | GOP |
|---|---|---|---|---|
| **Index proxy** | original | libx264 veryfast CRF 25, 540p, yuv420p, CFR | AAC 128k | — |
| **Preview** | the 540p proxy | libx264 **ultrafast CRF 27**, capped 480p | AAC 128k | `-g 48 -keyint_min 24` (~1.6s — Safari scrub accuracy) |
| **Final** | the ORIGINAL | libx264 **veryfast CRF 20**, source resolution | AAC 192k | `-g 120` |

All outputs `yuv420p` + `-movflags +faststart` + `-progress pipe:1`. No hardware encoding, no thread pinning — ffmpeg defaults on ~1 vCPU. Watchdogs: 5400s wall clock, 300s no-progress stall kill (added after a wedged encode froze the only media slot for hours). ffmpeg stderr is merged into stdout because an un-drained stderr pipe once deadlocked a font-less Devanagari caption render.

## 14.3 The branded end card

Every **final** ends with a 2.5s card (black background, the Valmera robot + wordmark, "Edited by Valmera agent" — the robot redrawn as vector primitives at 8× supersample from measurements taken programmatically off the 180px favicon, because that favicon was the only robot art in existence). Fades 0.45s in / 0.35s out; the program is scaled/SAR-corrected to exact geometry and concatenated with the card + silence. Deliberately placed after grades/captions/fades (a b/w grade must not desaturate the brand red; a user's fade-out must not swallow the card) and outside the music mix. A missing card PNG never fails an export — it logs "BRAND CARD MISSING" and renders unbranded. `OUTRO_VERSION` is stamped into render meta for cache grandfathering: pre-card finals re-encode; pre-card previews still serve.

The agent is explicitly taught the card's honesty implications: it is not in the EDL, no tool can touch it, downloads are ~2.5s longer than the reported program duration, and "remove the outro" must never be answered by cutting the user's real footage.

## 14.4 Verification, caching, hygiene

- **Verification**: output duration must match the Timeline-computed program duration + outro within max(0.75s, 3%); the black-frame ratio must not exceed the source's own by more than 0.7 (legitimately-black uploads pass). Failure → one retry → hard error.
- **Cache**: keyed on (project, variant, EDL version, source sha) — safe because versions are immutable. For transcript-driven captions, a **caption fingerprint** (sha256 of index words) also must match, because the index row is mutable (transcript edits, self-heal re-index). The studio's "force" recovery path mints a fresh key and bypasses everything.
- **Key hygiene**: render keys are opaque and unique per render (`media/{project}/{job}-{hex}.mp4`) — never reused (a fixed key once mutated bytes behind a live presigned URL) and never containing "renders/" or "preview_" (**ad-blocker bait** — proxies with opaque keys played in sessions where renders didn't). Superseded renders of the same (variant, version) are pruned best-effort.
- Every render emits a 3×3 contact sheet over the program (excluding the card, or the vision self-check would flag the branding as a black-frame defect), which feeds the agent's post-render self-check.

---

# 15. Backend API, Database, Storage & Credits

## 15.1 The API surface (`video_bp`, all JWT-authenticated except `/video/health`)

**Projects**: `POST /projects` (creates chat session + project), `GET /projects` (last 100, with has_video), `GET /projects/<id>` (project + assets + video + indexed + latest EDL + latest job per type), `PATCH /projects/<id>/title`, `DELETE /projects/<id>` (409 if jobs are running; deletes DB child-first; **re-points shared content-addressed index rows to a surviving project** before cascade; then best-effort R2 prefix deletion — "DB rows are the source of truth").

**Uploads**: `POST /projects/<id>/uploads` (validate → presign single PUT ≤64MB or multipart with 64MB parts, 15-min expiry) and `POST /uploads/complete` (tenant prefix check → **idempotency first** so retried completes never 400 → HEAD + size re-check → magic-byte sniff → row-locked dedupe insert → enqueue index for originals). Limits: video .mp4/.mov/.m4v/.webm/.mkv ≤ 2 GB; clips ≤ 500 MB; music .mp3/.wav/.m4a/.aac/.ogg ≤ 50 MB; images .png/.jpg/.jpeg/.webp ≤ 10 MB.

**Index/transcript**: `GET /index/status`, `GET /index` (trimmed for the transcript panel), `PATCH /transcript {sentence_id, text}` — re-tokenizes corrected words proportionally across the sentence's time span so karaoke captions don't desync, recomputes all word indices, refuses (409) while an index job runs, and — the multi-tenant edge — **refuses when another account's project shares the same content hash** (a correction must never mutate a stranger's transcript).

**State**: `GET /projects/<id>/state?after_id=N` — the consolidated poll powering the entire studio (§17), including the bounded self-heal re-index logic.

**Chat**: `GET /messages`, `POST /messages {text ≤4000, client_msg_id, attachments ≤4}` — idempotency before rate-limit (20/hr) before busy-check (one agent turn per project) before credits gate (`≥1.0` credit, only when indexed — pre-index concierge chat is free) before capacity check, so no path persists an orphaned message or double-charges.

**EDL ops** (`POST /projects/<id>/edl {op, args}`) — the user's direct-manipulation channel, refused while an agent turn runs: `set_frame`, `insert_media`, `set_insert_duration`, `move_insert`, `remove_insert`, `add/move/remove_voiceover`, `add/move/remove_music`, `move/remove_sfx`. Same snapping and remapping code as the agent; stale-id ops are idempotent no-ops; a real change appends a `created_by:'user'` version, auto-enqueues a preview, and posts a "you → EDL vN" activity row.

**Renders**: `GET /edls`, `GET /edls/<version>`, `POST /render/final {edl_version}` (**this endpoint IS the user-confirmation gate** — the agent can only render previews), `POST /render/preview {edl_version, force?}` — non-forced joins an in-flight render; `force` bypasses the render cache with a durable cap of 4 forced re-encodes per version per hour (each is a full re-encode of the original on the single global media slot; past the cap the API says honestly "re-encoding again genuinely will not help").

**Telemetry**: `POST /client-event` — player_error / player_error_probe / player_recovered / attach_failed; sanitized (≤20 keys, scalars only, numbers range-checked after a 4,000-digit JSON int sailed past an isinstance check), 60/user/hr, **never errors back to the user**. Exists because media bytes go browser↔R2 directly, so a client `<video>` failure otherwise leaves no server trace (the July 18 incident: a perfectly good render in R2 that a browser refused to play).

**Assets**: `GET /assets/<id>/url?download=1` — presigned GET, **6-hour TTL** (15 minutes killed the player src mid-session), with attachment disposition for downloads.

## 15.2 Database schema (6 idempotent migrations)

- `projects(id, user_id→users, title, chat_session_id, created_at)`
- `assets(id, project_id, kind ∈ original/proxy/audio/thumb/sheet/render/music/image_ref/video_clip, storage_key, bytes, duration_s, width, height, fps, sha256, meta jsonb)`
- `indexes(id, project_id, video_sha256 UNIQUE, json jsonb, pipeline_version int)` — one row per unique file content, shared across projects/accounts
- `edls(id, project_id, version, json, created_by ∈ user/agent, UNIQUE(project_id, version))` — append-only
- `video_jobs(id, project_id, user_id, type ∈ index/preview/final/agent_turn, payload, state ∈ queued/running/done/failed, progress, error, result, attempts, heartbeat_at)` — the queue itself, claimed via `FOR UPDATE SKIP LOCKED`
- `llm_calls(id, project_id, job_id, purpose, model, request jsonb, response jsonb, prompt_tokens, completion_tokens)` — every model call ever made, payloads capped at 200KB/side and key-redacted; purposes: agent, honesty_regen, vision_look, vision_selfcheck, vision_caption, concierge, image_gen, image_edit, sfx_gen, video_gen, index_greet
- `client_events(id, user_id, project_id, kind, asset_id, detail jsonb)`
- `chat_messages` + `meta jsonb` (roles user/assistant/**activity**; unique partial index on `(session_id, meta->>'client_msg_id')` for idempotent sends)
- `users` gains: `credits_daily (20), credits_daily_reset, credits_bonus (150), credits_monthly, credits_balance, credits_monthly_limit, is_subscribed, plan`

## 15.3 Storage layout (R2)

`originals|music|images|clips/{project}/{uuid12}.ext` (uploads) · `proxies/{project}/{sha}.mp4` · `audio/{project}/{sha}.wav` · `thumbs/{project}/{sha}/shot_N.jpg` · `sheets/{project}/{sha}/sheet_N.jpg` · `media/{project}/{job-stamp}.mp4` (all renders; opaque per-render keys) · `generated/`, `generated_sfx/`, `generated_video/`, `fetched/` (AI + imported media). Presign TTLs: uploads 15 min, playback 6 h, admin 15 min, worker→provider handoffs 1 h. CORS: valmera.io origins + localhost, `ExposeHeaders: ETag` (required for multipart). Deletion iterates 14 known prefixes — a prefix missing from that list silently orphans bytes.

## 15.4 Credits & plans

Three hidden pools shown as one balance, spent **daily → bonus → monthly**: daily 20/day (reset per calendar day, never accumulates), bonus 150 one-time at registration, monthly from the plan (Plus $20 → 800, Pro $50 → 2,400; retired grandfathered tiers Ultra 5,000 / Titan 10,000 / Ace 30,000). Monthly is wiped and refreshed on each Paddle renewal and **clawed back to 0 on cancel/refund**. 1 credit = $0.01 of real model cost, minimum 1 credit per turn. **Only agent turns are charged** — indexing, previews, finals, and concierge chat are subsidized infrastructure. The gate is a floor check (`balance ≥ 1`), not a reservation; the in-turn ceiling is balance + 3 grace credits.

---

# 16. Worker Orchestration: Jobs, Lanes, Retries, Crash Recovery

`worker/main.py` is a pure Postgres poller. Beyond the three lanes (§2.4), the machinery:

- **Claiming**: one `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`; eligible = queued, OR running with a heartbeat older than 120s (stale reclaim) — with attempts under the per-type max. **Priority within the media lane: preview → final → rest**, so an interactive preview never waits behind an export.
- **Heartbeats**: one daemon thread beats all active jobs every 20s.
- **Retries**: index/preview/final get 3 attempts; **agent turns get exactly 1** (side effects; replay would re-apply work). Exhausted jobs get type-specific, carefully-worded chat notes ("Press Download to try again"; the index-death note deliberately does NOT blame the user's file; a *forced* preview failure gets its own note that doesn't misdirect the user into re-editing).
- **Graceful shutdown**: Render sends SIGTERM before every deploy; in-flight non-agent jobs are released back to queued **with the attempt refunded** (`attempts−1`) — a planned deploy must not consume an attempt (a real customer's 24-minute index "died its third and final death from the redeploy that set an env var"). Agent turns are excluded and die honestly via the reaper.
- **The reaper**: every 60s, stale jobs with no attempts left → failed + an honest chat note, so no UI spins forever (a customer once watched a dead "Analyzing…" spinner for 88 minutes).
- **Boot hygiene**: `_sweep_tmp()` deletes orphaned workdirs (gigabytes of downloaded originals left by OOM SIGKILLs).
- **No user-facing cancel** exists for running jobs, and nothing checks for cancellation mid-agent-turn — the only stops are timeout, budget, step limit, model completion, or `ask_user`.

---

# 17. The Studio Frontend

One 3,899-line client component (`studio/page.js`), written — like the worker — as a war-story ledger where nearly every guard cites the production failure it fixes.

## 17.1 Layout & state

Desktop: fixed 380px chat panel + flexible workspace (macOS-window-framed): chrome bar (SOURCE/PREVIEW label, aspect-ratio + fit pills, version stepper ◀ N/M ▶, Replace, Download) → video stage + custom transport → TimelinePanel → TranscriptPanel. Mobile portrait: chat/preview become exclusive screens, with a compact player also mounted atop the chat pane so phone users can watch and export without switching. No state library — ~40 useState atoms + ~15 refs (sequence tokens against async staleness, a version-EDL cache, bounded re-render budgets). The timeline is wrapped in an error boundary so timeline-math crashes degrade to an inline "Retry" notice, not a dead page.

## 17.2 Polling and optimism

Everything rides `GET /state?after_id=N`: **2s while busy** (any job active, plus a 10s cooldown), **8s idle**. Messages merge by id with optimistic local bubbles replaced when the server echoes their `client_msg_id`. User timeline ops apply their returned EDL **immediately** ("timeline must react on click, not on the next poll"); ratio changes reshape the player wrapper before the re-render lands.

## 17.3 The player and its recovery ladder

Plain `<video>` + presigned MP4 URLs — no HLS, no MSE. Three sources: the 540p proxy (raw footage, source time), per-version preview renders (program time), finals (downloaded, not played). A newly finished preview auto-attaches (4 presign attempts with backoff) and **muted-autoplays exactly once** — so burned captions visibly animate; a paused identical-looking first frame made real users conclude "captions didn't apply." Custom transport exists because Safari's floating native controls sat on top of the timeline.

Failure ladder, keyed per asset so it terminates: retries 1–2 re-presign with cache-busting fragments; retry 3 falls back to the proxy **with an honest amber chip** ("Showing original — your edit didn't load"). MediaError code 4 → an unmissable overlay: "Your edit is finished — this browser just can't play the preview here" + a big **Download your video** link (the Safari/iOS escape hatch). Network errors → a **forced re-encode** (cache-bypass), budget-bounded to 1/asset and 2/visit client-side plus the server's durable 4/version/hour. Every failure fires telemetry, including a 2-byte Range probe to capture the actual HTTP status the server otherwise never sees.

## 17.4 Upload UX

Dropzone + paperclip + drag/paste + timeline drop + a landing-page pending file that survives the Google OAuth full-page redirect via IndexedDB (`pendingUpload.js`, 30-minute staleness cap, read-and-delete semantics so a ghost re-upload can never fire). Multi-file drops thread a mutable batch so later files don't spawn junk projects. Part uploads retry 3× on 5xx/timeouts only (progress rewinds so the bar never lies); completes retry 3× (server-side dedupe makes this safe) — "a single blip here used to throw away whole multi-GB uploads."

**Progress honesty**: upload maps to 0–50%, index progress to 50–100%, ONE number shown in three places; a queued-but-not-running index shows "Waiting in line to be analyzed" with **no percentage** — a real user watched a bar frozen at 50% for 63 minutes and churned. Stage labels map index progress ("Transcribing every word", "Understanding the visuals"…).

## 17.5 Chat, versions, export

Suggestion chips for known-good asks; activity rows collapse into "N editing steps" expanders; `ask_user` messages get an amber NEEDS-YOUR-ANSWER pulse; `credits_exhausted` renders an inline Upgrade panel on the exact message. Markdown is rendered with raw HTML escaped first and `javascript:`/`on*` stripped — explicit defense against the agent's own output being a stored-XSS vector. Every assistant bubble carries ▲/▼ feedback buttons (persisted to message meta — "the ground-truth training signal").

The **version stepper** pins any historical version, fetches its EDL (cached), attaches its preview or renders one on demand; new renders never yank focus while pinned. **One green Download button** starts the final render if needed and **auto-downloads when it lands** (armed per requested version; disarmed on project switch, job failure, or if the user moved to another version — "a surprise download minutes later is worse than none").

## 17.6 Direct manipulation vs. chat-only (the current boundary)

Users can directly: scrub; drag/remove/add inserts (snapped to keep boundaries with a white snap indicator), music, voiceover; remove SFX pins; set image-insert durations; drop OS files onto the track (video/image → insert; audio → ducked music); switch ratio/fit; and edit transcript sentences inline (which re-tokenizes, refetches, and auto re-renders when captions are active — built because whisper mishears brand names: "valmera.io" → "Valmer de laio").

Display-only (chat is the editor): speed badges, overlays and text items (tooltips say "ask in chat to change it" — deliberately, because the backend has no remove_overlay/remove_text op: "a delete button that can't delete would be a lie"), fx chips, and cut notches. **No direct trim handles on kept footage, no caption text editing outside the transcript, no effect/zoom/grade manipulation by hand.**

---

# 18. Observability, Admin & Testing

## 18.1 The admin surface (`/admin/video/*`, gated to the founder's email)

- **Overview**: per-user rollups (jobs, storage, tokens, est. cost), 14-day trends, ops counters — including **honesty telemetry** (false claims caught, corrective notes, fallback replies), NO-CHANGE counts, job failure rate, median queue wait, per-stage timing medians (index: whisper/proxy/shots; renders: download/encode/upload; turns: llm/total), an attention feed (failures + stuck jobs), and a models panel comparing configured env vs. **observed** models from llm_calls ("am I really on Grok?").
- **Project inspector**: full chat, all EDL versions with JSON, jobs with payloads/results, per-turn grouping (user message + activity + llm_calls + honesty verdicts + credits + resulting version), unserved-message detection (a user message no agent job ever referenced — "the strongest asked-and-got-silence signal"), and render-trigger attribution (USER edited vs AGENT rendered vs forced).
- **Model I/O**: paginated full request/response payloads per call; vision calls presign the exact contact-sheet images the model saw.
- **Costs, users, cohorts**: 30-day per-user-per-day cost tables; per-user credit pools and ledgers; signed_up → uploaded → messaged → exported → paid cohort funnels.

## 18.2 Deterministic QA (`worker/audit.py`)

LLM-free checks stamped into results: mid-word boundary detection (with snap suggestions), outward word-snapping, and regression warnings on keep-list replacements (re-included cut material annotated "mostly silence" / "verbatim duplicate of sentence X").

## 18.3 Testing

`scripts/integration_test.py` (1,166 lines) is a full E2E acceptance harness run against docker-compose (Postgres + MinIO + API + worker) or a no-Docker variant with **`scripts/fake_llm.py`** — a scripted OpenAI-compatible server that reads real tool results and plays scenarios *including deliberately dishonest ones*, so the honesty machinery is exercised keylessly. The suite covers: presigned upload → index assertions → chat idempotency → false-claim catch/regeneration/fallback → cut/restore diffs → caption styles → UI op idempotency → 402 on zero balance → fabrication honesty on missing music → mid-take insert word-edge splitting → effects render → stale-pipeline re-index → final faststart byte check → render-cache hits. Unit tests pin the EDL v2 semantics, graphics compilation, timeline golden vectors, media classification, and net-fetch deadlines.

---

## 18.4 The August 13 token spike: measured cause and local fix

This was call multiplication, not one mysteriously large prompt. Production job **9169** made **196 model calls** in about **59 minutes**: **183 agent dispatches**, **8,203,225 input tokens**, **93,465 output tokens**, **42 `apply_edit_recipe` calls**, **39 `get_edl` rereads**, and **14 preview requests**. Its project accumulated **97 EDL versions** during the run. Prompt caching was often high, but cached tokens still made the job slow and the raw admin total enormous.

The activity stream shows the mechanics. Six recipes aborted after earlier operations had been staged; eight more were rejected before execution. Four aborts came from applying the harmless identity `rate=1` to an image inside a uniform image/video patch. Several batches tried transaction-unsafe helpers such as `add_title_card`/`add_color_screen`; the deployed recipe catalog at the time also omitted ordinary existing-asset insertion. The editor then reread the EDL, rebuilt a smaller recipe, rendered, disliked or contradicted the result, and repeated. This was taste churn amplified by a fragile low-level program.

The current local code attacks those measured causes without imposing a creative cap:

- one preflighted atomic compiler validates every operation's shape, required arguments, alias declarations and backward-only references before staging;
- `save_as` + `{"$ref":"alias"}` removes the create → reread EDL → patch-id round trip;
- already-fetched music and SFX placement/gain can travel with picture and motion in one EDL commit;
- video-to-audio extraction is explicitly refused inside a transaction so an aborted recipe cannot leak an asset side effect;
- neutral mixed-media dialect (`rate=1`, `clip_start_s=0`, full-frame crop, mute=false, rotation=0 on a still) is normalized instead of aborting valid sibling work;
- production metadata now records recipe calls, commits, aborts, committed operations and resolved references beside agent dispatches, versions, previews, cache ratio and cost.

Verification for this local phase: a realistic eight-operation picture/type/vector/music/SFX recipe commits as **one** EDL version; forward references fail before any staging; extraction cannot escape an aborted recipe; the focused affected suite passed **140 tests**, the backend suite passed **228**, and the full worker suite passed **1,262**. These changes are **not a deployed result or proof of 10× quality** until they are pushed and compared by code-version/editorial-family cohorts plus blinded human-reference benchmarks.

---

# 19. Security Posture

What's genuinely strong: the SSRF sandbox around URL fetching (resolve-first + peer verification + redirect re-validation + absolute deadlines); whitelist-only bundled-asset resolution; presigned-URL-only media access with tenant prefix checks; magic-byte upload sniffing; markdown sanitization of agent output; payload redaction in llm_calls; idempotency keys everywhere; cross-tenant transcript-edit refusal on shared content hashes; layered, incident-derived rate limits (20 msgs/hr, 3 concurrent jobs, 4 forced renders/version/hr, 60 events/hr, self-heal caps).

The known soft spots, all documented in the repo itself:

1. **`SECRET_KEY` fails open** to the literal `"supersecretkey"` if unset — every JWT forgeable → full account takeover. Pure config risk; must be verified on both Render services.
2. **Paddle webhook fails open** when `PADDLE_WEBHOOK_SECRET` is unset — an unsigned POST could forge a subscription. Code HMAC-verifies when present.
3. **The production Postgres URL, with password, is committed in CLAUDE.md.** Rotate it.
4. CORS is `origins="*"` with manual header echo (bearer auth keeps CSRF risk low, but it's maximally permissive).
5. **No egress policy on the worker** — the yt-dlp residual risk above.
6. Pricing constants triplicated across three files; drift = silent mischarging.

---

# 20. The Complete Limitations Catalog

Everything the product cannot do today, organized by layer. (Notably, the worker codebase contains essentially zero TODO/FIXME markers — these limits are either consciously fenced in prompt + tool text, or structural.)

## Editing model
1. **Single video track.** One main video (or one canvas insert sequence). Inserts splice only at keep boundaries (mid-take placement works by splitting the take at a word edge). No B-roll track, no picture-in-picture *tracks* — just the overlay layer stack.
2. **No crossfades/dissolves.** All seven transitions are duration-preserving junction effects; there is deliberately no xfade overlap model. The prompt: "True crossfades still do not exist — say so when asked."
3. **Keyframes exist only on overlay x/y** (max 24, five easings, no bezier). No keyframed opacity/scale/rotation/effects/audio.
4. **Nothing tracks motion.** Censor rectangles, overlays, and text are all static-position (or keyframe-drifted); no object tracking, face tracking, or auto-reframe-on-subject.
5. **No multi-cam, no markers, no nested sequences, no project-level assets reuse across projects** (assets are per-project; only the index is content-shared).

## Captions & text
6. Inserted/generated/fetched clips are **never transcribed** — no captions over inserts, and their content is invisible to search.
7. No SRT/VTT import or export. No custom font uploads (12 bundled families only). No emoji/sticker decorations. Text width is estimated, not shaped-measured.
8. Karaoke sliver-drop: under extremely fast speech, some words never get their own highlight frame.

## Audio
9. **No stem separation** — baked-in music is inseparable from speech.
10. **No TTS** — voiceover requires a user-provided audio file.
11. No denoise/restoration, no diarization/per-speaker control, no beat-matched music (library BPM is measured offline but unused; 17/24 tracks octave-ambiguous), no limiter on the mix (deliberate — sync over safety-clipping), video-overlay audio never plays.
12. The music library cannot serve "epic trailer" requests (0/24 tracks pass the measured bar) and cannot guarantee instrumental-ness.

## Rendering
13. Previews are sacrificial: 480p from a 540p CRF-25 proxy, ultrafast CRF 27. What the user evaluates is materially below what they export.
14. Slow motion duplicates frames (no optical-flow interpolation); below 0.6× visibly steps.
15. fps caps at 60 whenever the normalized path runs; every export carries the ~2.5s Valmera end card (not removable by users or agent).
16. No hardware encoding; no per-cut transition control (one global spec).

## Agent & context
17. **No cross-turn memory** beyond 20 truncated chat messages + the state block; prior tool results are gone each turn.
18. The honesty regexes are English-only and inherently brittle; a non-English reply bypasses the fabrication guard.
19. No mid-turn cancellation; `render_preview` can block an agent slot for up to 15 minutes polling the single media slot.
20. The spend cap is check-before-call — one expensive LLM call can overshoot the budget by up to a full call's cost.
21. `MAX_ATTEMPTS_AGENT=1`: a worker death mid-turn costs the user their request (honestly surfaced, but still lost).

## Infrastructure
22. **One vCPU, one media slot, one index slot, global.** All customers serialize: ~16 min to index a 19-min video, ~14 min per preview, 63-minute queue incidents on record. This dominates every UX metric today.
23. No user-facing job cancellation; no streaming agent responses (poll-based activity feed); no egress policy on the worker.
24. Renders/indexing are uncharged (subsidized) — cost scales with usage with no revenue guard other than turn credits.
25. Presigned upload of the original + full download to the worker per job (no partial/range reads); every render re-downloads inputs.

## Product surface
26. No share links or hosting — export is a download only. No direct publishing integrations (TikTok/YouTube), despite dormant `tiktok_sessions/` scaffolding in the repo root.
27. No team/collaboration features, no project sharing.
28. The timeline is read-mostly: no direct trim handles, no caption editing in place, no effect manipulation by hand; overlays/texts can't even be deleted by hand (no backend op).
29. Docs describe the pre-round-35 product (see §21).

---

# 21. Docs vs. Reality

The public docs (`/docs/*`) are **significantly behind the shipped code** — mostly *underselling*, which is the good direction to drift, but worth fixing:

**Docs undersell (shipped but never mentioned):**
- All **11 premium caption presets**, the 12 selectable fonts, per-word emphasis + automatic digit emphasis, the treatment system, chrome/chroma/glow layered effects, stack layout with overlapping leading and stagger, the six extra caption entrance animations, `karaoke_group_n` up to 8.
- The entire **text/motion-graphics layer** (titles, subtitles, lower thirds, callouts, big numbers, quotes, chapters, typewriter).
- **Speed ramps** (docs explicitly say "no speed ramps" — false since round 35), the five extra transitions (whip ×2, zoom_punch, glitch, flash — docs mention only dips), stylize looks, custom grading axes, mastering, overlays, SFX (built-in pack AND generated), AI **video** generation (docs say "no generated video footage" — false), URL fetching, the built-in music library (audio docs say "deliberately no stock library — licensing" — false: 24 CC0 tracks shipped), voiceover ducking modes, `apply_look`, `sound_design_pass`, `beat_align_cuts`, `punch_in_on_emphasis`.

**Docs claim limits that are now false:**
- "No text outlines or background bars" — every legacy caption has an outline; the box treatment and karaoke traveling box ARE background bars; graphics has panel boxes.
- "No custom fonts" is technically true for uploads but obscures 12 bundled families.

**Still-true limits docs get right:** no crossfades, no subject-tracked reframing, no SRT upload, no denoise, no share links.

Separately, both repos' agent-facing docs (`CLAUDE.md`, `AGENTS.md`) still describe the retired app-builder in their headline sections, with the video product relegated to a pivot note. Anyone (human or AI) onboarding from those files starts with the wrong mental model of the product.

---

# 22. Where to Take It: Improvement Opportunities Grounded in the Code

You asked for understanding in service of dramatic improvement. These are the highest-leverage directions the codebase itself points at, ordered roughly by (impact ÷ effort), with the enabling groundwork that already exists.

## 22.1 Infrastructure first: the queue IS the product experience
Every serialized minute is churn — the repo documents customers leaving over it. The fix is explicitly known ("the real fix is a bigger instance"): move the worker to 4–8 vCPUs, raise `MEDIA_SLOTS`/`INDEX_SLOTS`, and — architecturally more important — **split lanes into separately scalable services** (an index fleet, a render fleet, an agent fleet) since the job table + `SKIP LOCKED` claiming already supports N workers with zero code changes. Consider NVENC or a cloud transcoder for the proxy encode (~88% of index time) and previews. This one change compounds every other improvement.

## 22.2 Preview latency: stop paying 14 minutes per iteration
The deeper fix than hardware is **incremental rendering**. Because EDL versions are diffable JSON and the renderer is deterministic, most turns change a tiny region (a caption style, one cut). Options with existing groundwork: segment-level render caching (split the program at junctions, cache per-(segment, effects-fingerprint), concat with stream-copy); or a **client-side preview compositor** for the cheap 80% (the studio already has the full timeline math in JS — it could play cuts/speed directly against the proxy via seeking, overlay captions with a JS ASS renderer like JASSUB/libass-wasm, and only fall back to server renders for effects-heavy states). Sub-second feedback on cuts and captions would transform the editing feel from "email a lab" to "editor."

## 22.3 Direct manipulation parity
The timeline is read-mostly today, and the gap is now mostly plumbing, not architecture — the backend EDL-op pattern (validate → append version → auto-preview) is proven. Add ops for: trim handles on keep spans (`cut_range`/`restore_range` already exist as primitives), remove/move overlay and text (the UI literally has comments saying the delete button would lie today), caption style picker (a visual preset gallery driving `set_caption_style`), zoom/effect chips with delete, and undo-as-new-version buttons. Every op you add makes the product feel like a real editor to professionals while keeping the agent as the power tool.

## 22.4 Ship the caption system in the marketing
The premium caption engine (11 presets, treatments, chrome/chroma/glow, stack layouts) is competitive with CapCut/Submagic and is **completely absent from the docs and presumably the marketing site**. Zero engineering; pure revenue. Same for speed ramps, transitions, the text layer, AI video, and the music/SFX packs. Fix `/docs` and the comparison pages.

## 22.5 Wake up the dead assets
- `features.json` (3,765 lines of measured music features) is loaded by nothing. Wire it into `list_music_library` and a `pick_music(brief)` tool: tempo bands, energy arcs, `not_for` anti-tags — instant "smart music selection" feature at near-zero cost.
- The `AnimFloat` keyframe primitive is production-ready but applied to exactly two fields. Extending it to overlay scale/opacity/rotation, zoom strength, text position, and volume would unlock real motion design with the schema/validation machinery already built.
- Perception's beat grid + `beat_align_cuts` + `sound_design_pass` + `apply_look` amount to an "auto-edit" capability — one `make_it_pop` / montage mode away from being a headline feature.

## 22.6 Fill the loudest capability gaps (in order of user-ask frequency implied by ALTERNATIVE_HINTS and the prompt's own denials)
1. **Stem separation** (Demucs on the worker, or an API) — unlocks "remove the background music," per-speaker cleanup, and honest music replacement. The four-layer audio model slots a "separated music" layer naturally.
2. **TTS voiceover** (ElevenLabs is already integrated for SFX — the same account does voices) — turns the voiceover feature from "bring your own MP3" into a generator, and pairs with the script the agent can already write.
3. **True crossfades** — an xfade-based overlap model is the one transition users keep asking for; it requires timeline-math changes (overlap consumes duration), which is why it was deferred, but the three-clock model can absorb it with an explicit overlap field.
4. **Subject-aware reframing** (a face/subject detector writing cx/cy keyframes at index time) — "crop is centered" is the docs' own admitted weakness vs. Opus Clip.
5. **Caption translation / multilingual** — the index already detects language; the honesty layer needs de-anglicizing anyway (do both together: make reply verification language-aware or model-based).
6. **SRT/VTT export** — trivially derivable from `kept_words`, table stakes for pro editors.

## 22.7 Product-shape opportunities
- **Clipping/repurposing mode** ("turn this podcast into 6 shorts"): the index (sentences + shots + stress + energy) contains everything a highlight-finder needs; canvas + `set_frame` + captions produce the output. This is Opus Clip's entire business sitting latent in your pipeline.
- **Share links** (host the final MP4 behind a public URL + OG tags): small lift, big virality surface — every export currently dead-ends in a download.
- **Publish integrations** (YouTube/TikTok upload APIs — the dormant tiktok_state scaffolding suggests this was already contemplated).
- **Multi-ratio batch export** (the EDL is ratio-parametric; render 16:9 + 9:16 + 1:1 from one version in one job).

## 22.8 Hygiene that will bite later
Rotate the committed DB password and set `SECRET_KEY`/`PADDLE_WEBHOOK_SECRET` explicitly; de-triplicate the pricing constants into one imported module; rewrite CLAUDE.md/AGENTS.md around the video product; add worker egress policy; delete or archive the dead app-builder engine (it confuses every tool and human that reads the repo); and consider making the honesty layer model-graded rather than regex-graded before any internationalization.

---

## Closing

The system you have is unusually well-engineered for its stage: deterministic where it must be, honest by construction, observably instrumented end-to-end, and documented through postmortems rather than aspirations. Its constraints are equally clear: one small machine doing everything serially, a preview loop measured in minutes, a read-mostly timeline, and a set of headline capabilities (captions above all) that the outside world hasn't been told about. The architecture — immutable EDL versions, three-clock timeline math shared across three codebases, whitelist asset resolution, append-only observability — is a foundation that will carry all of the improvements above without structural rewrites. The bottlenecks are compute, iteration latency, and storytelling, in that order.

---

# 23. August 14 Current-State Agent & Quality Audit

This section answers the current product questions directly and supersedes the
July inventory where the implementation has moved on. It distinguishes what
the general editing model receives, what a specialist reviewer can inspect,
what is merely measured, and what is not available.

## 23.1 What the editing agent receives on a fresh turn

| Input | What reaches the model | Later turns and overflow |
|---|---|---|
| Main video | A current labeled filmstrip (up to 36 tiles), duration/frame/audio facts, transcript when it fits, shot boundaries, silences, motion summary, current EDL and program map | Rebuilt on every fresh user turn. Long transcript text is paged, but remains available through transcript/search tools. |
| Uploaded video clips | Labeled filmstrips for indexed clips, clip duration/role/storage key, and their transcript/perception through read tools | Rebuilt every turn. Current-message attachments and clips already used in the edit are prioritized. The whole visual turn budget is 60 video tiles; overflow clips remain explicitly named and can be opened with `look_at_asset`. |
| Uploaded still images | The actual image pixels, one labeled still per selected asset (up to 20 per turn) | Rebuilt every turn, including images uploaded in earlier messages. Current attachments and used assets are prioritized. Overflow images remain named and retrievable; they are not falsely treated as seen. |
| Reference clips/images | The same real pixels plus a `STYLE REFERENCE ONLY` role. Indexed reference video also contributes measured cut rhythm, shot-duration distribution, energy shape, beat relationship and motion profile | Persists in project inventory across later turns. The prompt forbids inserting a reference merely because it is attached. |
| Uploaded audio-only file | Its filename/key/duration and an explicit warning that DB kind `music` may actually be music, voiceover/dialogue or SFX. Indexed transcript, tempo/energy and waveform facts are available | Raw audio is **not** continuously streamed into the general editing model. `review_audio`, music auditions, SFX auditions, and final-mix review can send bounded real excerpts to an audio-capable reviewer when configured. |
| Current edit | Full semantic EDL state, output-order program map, captions, recent versions, media already placed/unused, durable creative blueprint and family contract | Recomputed after every write. The EDL is authoritative; the model is not expected to remember an old timeline. |
| Chat history | Current request plus only the last four messages, each bounded | Older creative state belongs in the EDL/blueprint rather than being repeatedly re-tokenized as stale conversation. |
| Web/stock candidates | Metadata plus labeled thumbnail boards when visual delivery works | A search result is not an asset and cannot be placed. The downloaded rendition's real frames/motion are reviewed before a following reasoning step may place it. |

So the precise answer to “does it receive every uploaded image, including from
later turns?” is: **for normal-sized projects, yes, every current turn gets a
fresh labeled visual overview of the project's images and indexed clips, not
only the current attachment.** On very large libraries it receives a balanced
bounded visual slice, explicitly sees which files were omitted, and must call
`look_at_asset` before making a pixel-dependent decision about an omitted file.
That is a visual evidence budget, not a creative or placement cap.

## 23.2 What is cached and what is deliberately released

- Indexes are durable by media SHA plus pipeline version: transcript, words,
  shots, proxy/tile artifacts, audio perception, spatial samples and motion
  profiles survive turns and projects that reuse the same media.
- Reference grammar and image captions derive from durable asset/index state
  instead of paying to rediscover the same facts every call.
- Within one turn, editorial maps, perception, spatial data, motion analysis,
  search results and audio candidate measurements have per-turn caches.
- The stable filmstrip image block is placed before changing EDL state so an
  OpenAI-compatible provider can cache the large prompt prefix.
- After the first plan/write, broad filmstrip pixels are removed from the
  active conversation while their labels and a reopen instruction remain.
  Exact `look_at` pixels survive until they inform a committed write, then are
  similarly released. This prevents one image payload from multiplying across
  dozens of dispatch calls without claiming perfect visual memory.
- Only durable decisions (EDL + creative blueprint) persist between user
  turns. Hidden model reasoning does not.

## 23.3 Why token use spiked on August 13

The production trace is conclusive rather than inferred. Job 9169 / project
755 ran from 14:32–15:31 UTC and produced:

- 196 model-call rows, including 183 general agent calls;
- 8,203,225 input tokens and 93,465 output tokens;
- 42 `apply_edit_recipe`, 39 `get_edl`, and 14 `render_preview` calls;
- 97 EDL versions (122 through 218) in 59 minutes.

Of the 42 recipes, only 27 committed; 8 were rejected, 6 aborted and 1 was a
no-op. Four aborts came from applying neutral `rate=1` to still images; other
failures included non-transaction-safe tools and an older deployed recipe
catalog. The spike was therefore **loop multiplication**: the model repeatedly
re-read a large cached-prefix context while creating, reading, repairing and
re-versioning the edit. It was not evidence that one prompt suddenly became
eight million unique tokens.

The local fix now preflights an entire atomic recipe, supports `save_as` plus
explicit `{"$ref":"alias"}` references, accepts already-fetched music and
SFX, normalizes neutral mixed-media arguments, and commits a coherent
picture/type/vector/music/SFX treatment as one EDL version. Search, download,
generation and extraction remain outside the transaction because they have
external side effects. New recipe metrics make reject/abort/churn visible.
These changes are tested locally; they are **not deployed and not yet proof of
a 10× production improvement**.

## 23.4 Capability truth table

### Captions

The current caption system supports transcript-derived or manual captions,
word-safe program remapping, caption fixes/mutes, placement/size/font/color,
flow or stack layout, tracking/leading/background/outline/shadow, emphasis
words and treatments, and 20 named presets: `clean`, `documentary`,
`broadcast`, `retro`, `neon`, `podcast`, `reels`, `beast`, `karaoke`,
`elegant`, `spotlight`, `stacked`, `iridescent`, `chrome`, `editorial`,
`fashion`, `luxe`, `impact`, `lyric`, and `classic`. Animations include fade,
pop, slide, punch, blur, whip, flash, rise/drop, elastic, bounce, swing and
zoom-blur. Captions are burned via libass; caption timing and collision audits
operate on the exact compiled artifact. Limitations remain: no SRT/VTT import
or export, no arbitrary uploaded font file, no per-word manual Studio editor,
and no continuously subject-tracked caption band.

### Designed text, vectors and animation

Designed text has seven templates (`title`, `subtitle`, `lower_third`,
`callout`, `big_number`, `quote`, `chapter`), 13 bundled font families,
named entrances/exits including typewriter, subject-aware fixed-band placement,
caption suppression under designed phrases, and a one-call kinetic typography
pass synchronized to real transcript words. Text, overlays and renderer-native
vectors (`rectangle`, `ellipse`, `line`, `arrow`, `ring`, `progress`) share
element-local keyframes for x, y, scale, rotation and opacity with linear,
in/out, in-out and hold easing. There are also zoom paths, aspect shifts,
screen corner-pin/takeover moves, text-behind-subject mattes, panel composition,
freezes, grades, stylize effects and duration-preserving transitions.

This is a capable 2D motion-graphics system, not After Effects. It cannot do
general object tracking, arbitrary Bézier paths/shape morphing, particles,
3D camera/type, expressions, plugins or arbitrary custom fonts. “World-class”
therefore has to come from selection, composition, synchronization and
restraint inside these primitives—not pretending every AE technique exists.

### Audio, music and sound effects

The main editor receives transcript and measured audio facts. It can call a
specialist to hear bounded real source/asset/program excerpts; it does not
hear the whole soundtrack continuously by default. Music search currently
uses Jamendo (when keyed) and Openverse; SFX search uses Openverse/Freesound.
Candidates carry duration, authorship and license obligations, are measured,
and can be compared by an audio-capable reviewer before download/placement.
The renderer supports music, SFX, uploaded voiceover, gain, fades/loop/offset,
ducking, source volume automation, stem mix when separation exists, and
master loudness. The actual rendered mix can be heard by an independent final
audio reviewer.

Important limits: no generated TTS voice by default, no guarantee that an
audio reviewer is configured, no full-song copyrighted catalog license, and
no basis for claiming unheard portions sound good. A platform-trending sound
often still needs to be added/licensed in the platform itself.

### B-roll and web fetching

`research_broll` accepts story-wide moments with a query, alternate semantic
routes, narrative purpose, placement and desired duration. It searches
Pexels/Pixabay video/photo when configured plus Openverse photos, matches the
output orientation, interleaves providers, removes duplicates and presents a
balanced candidate board across the whole story. Selection is explicitly by
relevance, authenticity, composition, light/color and sequence diversity—not
search rank. After `add_stock_media`, the downloaded file is probed and its
actual frames/motion are delivered before placement is allowed on the next
reasoning step.

The agent cannot fetch “anything on the web.” It can ingest an accessible
direct URL or supported platform media when URL fetching/extraction is enabled,
and it has stock/Openverse catalogs. Private/login media, bot walls, dead URLs,
DRM, provider/API outages, unsafe/private-network targets, size/duration caps,
copyright/licensing and missing downloadable renditions remain real bounds.
SSRF checks apply to every fetch. For topical material beyond stock, success
depends on the `find_footage`/URL route and actual provider availability; the
agent must not invent a result.

## 23.5 Why the product still lacks professional judgment

The renderer is not the primary taste bottleneck. The missing product layer
has been a decision system:

1. A vague brief lets one generalist model choose the first plausible local
   treatment while it is also operating tools.
2. Reference grammar and family rules exist, but historically advised the
   model rather than selecting and binding one treatment.
3. A clean preview proves “not visibly broken” more readily than it proves
   “this edit is more specific, coherent and compelling than another edit.”
4. Search relevance can be acceptable while the chosen B-roll is generic;
   waveform fit can be acceptable while music taste is ordinary; a caption
   preset can render correctly while being wrong for the speaker/brand.
5. There is a blinded pairwise benchmark runner, but no real multi-family
   candidate-vs-human corpus checked into this workspace. There is therefore
   no evidence yet that a release beats a human reference, much less by 10×.

The current local implementation closes part of (1) and (2) without adding a
second permanent model call. A substantial first-call blueprint now records:

- one named treatment;
- observed `decision_basis` facts;
- relationships to transfer from an actual reference;
- cross-department `coherence_rules`;
- brief reasons materially plausible alternatives lost;
- an ordered sequence map whose timed beats cite real transcript/shot IDs.

Timed source beats with invented or unrelated evidence IDs are mechanically
rejected. This contract feeds the main editor, music/SFX selection, motion
direction and the independent visual, story and final-audio reviewers. Narrow
repairs inherit the established language without paying a new director call or
being forced through whole-program paperwork.

## 23.6 Concrete path to “one-shot professional”

This should be treated as an evaluation-driven product program, not a prompt
rewrite.

1. **Build the real benchmark corpus first.** For each priority family
   (podcast conversation, talking-head social, narrative vlog, product demo,
   voiceover montage, action/music), preserve the same source + brief, a strong
   human reference, the current Valmera result and blind human pairwise labels.
   Use the existing two-order visual/story/audio evaluator; never collapse
   regressions into one flattering aggregate score.
2. **Make the treatment contract the execution authority.** The first call
   should pick one source-grounded route; atomic compilation should express its
   picture/type/motion/audio pass in as few coherent versions as evidence
   permits. Keep unlimited tools available, but measure semantic progress,
   recipe aborts, versions per finished edit, model calls, wall time and cost.
3. **Turn B-roll into retrieval + ranking + sequence casting.** Learn from
   accepted/rejected human candidate choices. Rank visual specificity,
   authenticity, composition match, motion direction and sequence diversity;
   require actual rendition review. Generate a custom visual only when
   retrieval cannot supply the needed proof, not as a generic fallback.
4. **Turn sound into supervision, not decoration.** Compare real music/SFX
   excerpts; model entry/exit, contrast and motif reuse across the sequence;
   review the real final mix. Silence and no-SFX must remain valid winners.
5. **Promote animation from presets to grammar.** Use the shared keyframe
   primitive to express a small coherent motion vocabulary per treatment;
   attach movement to rhetorical/visible events and preserve stillness as
   contrast. Add primitives only when the benchmark repeatedly proves a real
   capability gap.
6. **Make podcast cutting a discourse task.** Score complete question/answer
   arcs, references, escalation and payoff from the whole transcript; retain
   boundary context; independently review the assembled transcript. Optimize
   for a coherent conversation, never “top isolated quotes.”
7. **Release behind family-level gates.** A candidate build ships only when it
   wins or ties the previous build on blind human and model-separated visual,
   story and audio dimensions, with no material family regression and with
   cost/latency inside the target envelope.

The north-star claim should be earned with measurable outcomes: publishable on
first render, pairwise win rate against the previous build and a strong human
reference, story-defect rate, irrelevant-B-roll rate, caption collision and
correction rate, audible mix/SFX defect rate, calls and EDL versions per
finished edit, p50/p95 wall time, credits, render retries, and user acceptance
without a corrective chat turn. “10×” should mean a large verified improvement
across quality, speed, cost and revision burden—not ten times more effects.
