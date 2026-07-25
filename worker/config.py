"""Worker configuration — everything comes from env so the service can be
re-pointed (different LLM provider, GPU whisper box, other bucket) with zero
code changes."""

import os

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Object storage (S3-compatible; default deployment target is Cloudflare R2)
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "auto")

# LLM — OpenAI-compatible only. Default: xAI Grok (api.x.ai/v1). The whole
# stack (agent tool-calling, vision, concierge) is OpenAI-compatible, so
# pointing OPENAI_BASE_URL + OPENAI_API_KEY at any compatible provider is all
# that's needed. To run Grok you ONLY set OPENAI_API_KEY (an xAI key); the
# defaults below already select Grok 4.5. (To go back to DashScope/Qwen, set
# OPENAI_BASE_URL, AGENT_MODEL, VISION_MODEL and the LLM_PRICE_* below.)
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.x.ai/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "grok-4.5")
# grok-4.5 is multimodal, so it doubles as the vision model. Empty string
# disables all vision features gracefully. Set to a cheaper vision model if
# xAI ships one.
VISION_MODEL = os.getenv("VISION_MODEL", "grok-4.5")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "90"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))
# Vision (look_at) is the slowest thing the agent does, so it gets a MORE
# generous per-call timeout than the text agent (grok multimodal latency is
# spiky) — retries stay at the client default. The agent isn't capped on how
# many looks it may take; the accurate transcript (so it stops lip-reading)
# plus the longer turn wall are what keep vision from running away.
VISION_TIMEOUT_S = float(os.getenv("VISION_TIMEOUT_S", "120"))

# Image generation. Two backends are supported and auto-detected from
# OPENAI_BASE_URL (see worker/llm.image_provider):
#   * OpenAI-compatible /images/generations (xAI Grok, default) — text-to-image
#     ONLY; it cannot restyle/edit an existing frame or image.
#   * DashScope native multimodal-generation — text-to-image AND frame/image
#     restyling (set OPENAI_BASE_URL back to dashscope, or IMAGE_API_URL).
# Empty IMAGE_GEN_MODEL disables the generate_image tool everywhere gracefully,
# same contract as VISION_MODEL.
# MODEL ID HISTORY — image gen has 404'd on xAI TWICE now, silently, for weeks:
#   grok-2-image        -> never a valid xAI id (404 "not-found"), Jul 17-21 2026
#   grok-2-image-1212   -> valid once, DEPRECATED 2026-02-24; the round-33 "fix"
#                          swapped one 404 for another and was never live-checked
#   grok-imagine-image* -> the Grok Imagine family that replaced it (Jan 2026).
# *WHICH id: the live 404 body said "use grok-imagine-image", but xAI's official
# docs (docs.x.ai/developers/model-capabilities/images/generation, checked Jul 24
# 2026) use grok-imagine-image-QUALITY in EVERY code sample for /v1/images/
# generations — the bare name is ambiguous/contested. We use the id the docs
# actually call, to avoid a third silent 404. (grok-imagine-image-pro was
# retired 2026-05-15.) Price below tracks the -quality tier (~$0.055/image).
# Every attempt is logged to llm_calls (purpose 'image_gen') with the model and
# the error, so `SELECT model, count(*) FILTER (WHERE response->>'error' IS NOT
# NULL), count(*) FROM llm_calls WHERE purpose='image_gen' GROUP BY 1` tells you
# in one query whether the id is live. RUN IT after deploy — a wrong id here is
# invisible in the UI (the agent just says it couldn't make an image). If it
# still 404s, the error body names the id xAI wants; set IMAGE_GEN_MODEL to it
# on the worker (no redeploy) and sync IMAGE_PRICE_USD to its real price.
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "grok-imagine-image-quality")
# Frame/image restyling model — only used by the DashScope backend. Empty on
# the OpenAI/xAI backend (which has no image-edit endpoint).
IMAGE_EDIT_MODEL = os.getenv("IMAGE_EDIT_MODEL", "")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "")
IMAGE_TIMEOUT_S = float(os.getenv("IMAGE_TIMEOUT_S", "150"))
# 8 (was 4): the real bound is the user's credit budget (_gen_budget_reject
# prices every image before spending); this stays only as a backstop against
# a runaway generation loop.
MAX_GENERATED_IMAGES_PER_TURN = int(
    os.getenv("MAX_GENERATED_IMAGES_PER_TURN", "8"))

# ── AI sound-effect generation (ElevenLabs) ──────────────────────────────────
# A dedicated provider — xAI/OpenAI have no text-to-audio endpoint. Empty key
# disables the generate_sfx tool everywhere gracefully (same contract as image
# gen); the built-in CC0 pack (add_sfx) stays available regardless.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVEN_SFX_URL = os.getenv(
    "ELEVEN_SFX_URL", "https://api.elevenlabs.io/v1/sound-generation")
ELEVEN_SFX_MODEL = os.getenv("ELEVEN_SFX_MODEL", "")  # "" = provider default
SFX_MAX_DURATION_S = float(os.getenv("SFX_MAX_DURATION_S", "22"))
SFX_TIMEOUT_S = float(os.getenv("SFX_TIMEOUT_S", "60"))
# 10 (was 6): the credit budget is the real bound (each sound is priced
# before the provider is called); this is a runaway-loop backstop only.
MAX_GENERATED_SFX_PER_TURN = int(os.getenv("MAX_GENERATED_SFX_PER_TURN", "10"))

# ── AI video generation (fal.ai aggregator) ──────────────────────────────────
# NOT OpenAI-compatible — its own REST (queue.fal.run/{model}). One FAL_KEY,
# model chosen entirely by env (swap tiers without a deploy, exactly like
# IMAGE_GEN_MODEL). Default = Kling 2.5 Turbo Pro image-to-video (best-reputation
# animate-a-still). Empty key disables the generate_video tool gracefully.
FAL_KEY = os.getenv("FAL_KEY", "")
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "fal")
VIDEO_GEN_MODEL = os.getenv(
    "VIDEO_GEN_MODEL", "fal-ai/kling-video/v2.5-turbo/pro/image-to-video")
FAL_QUEUE_URL = os.getenv("FAL_QUEUE_URL", "https://queue.fal.run")
VIDEO_MAX_SECONDS = float(os.getenv("VIDEO_MAX_SECONDS", "10"))
# Kept UNDER AGENT_TURN_TIMEOUT_S (default 720) so a slow fal job fails inside
# the turn instead of pinning a scarce agent slot past the turn budget on the
# 1-vCPU worker (the round-19/28 slot-starvation class). Submit(30) + poll(240)
# + response(30) + download(90) ≈ 390 < 720. Raise this if you use a slower
# video model, keeping it under AGENT_TURN_TIMEOUT_S.
VIDEO_POLL_TIMEOUT_S = float(os.getenv("VIDEO_POLL_TIMEOUT_S", "240"))
VIDEO_POLL_INTERVAL_S = float(os.getenv("VIDEO_POLL_INTERVAL_S", "6"))
MAX_GENERATED_VIDEOS_PER_TURN = int(
    os.getenv("MAX_GENERATED_VIDEOS_PER_TURN", "3"))

# The index pipeline version is a CODE CONSTANT in schemas.py, shared with
# the backend (which loads worker/schemas.py directly) — bump it there, by
# commit, whenever index output changes. It is deliberately NOT an env var:
# the env-per-service version drifted between backend and worker for a day
# (Jul 16-17 2026), which re-indexed every project on every open in an
# infinite loop and starved two real customers' jobs off the box.
from schemas import PIPELINE_VERSION  # noqa: E402,F401

# Transcription provider. faster-whisper runs on the worker's OWN CPU — free and
# private, but 'medium' at int8 is weak exactly where this product lives (loud
# music, crowds, one word over a bar) and it is the slowest step of indexing.
# Deepgram nova-3 is materially better on that audio, returns the word-level
# timestamps the EDL needs, and takes whisper off the CPU entirely.
#   DEEPGRAM_API_KEY set  -> deepgram, with whisper as an automatic fallback
#   unset                 -> whisper, exactly as before
#   TRANSCRIBER           -> forces either side ('deepgram' | 'whisper')
# NOTE: switching providers changes the index's OUTPUT, so bump
# schemas.PIPELINE_VERSION in the same commit to rebuild existing transcripts.
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()
TRANSCRIBER = os.getenv(
    "TRANSCRIBER", "deepgram" if DEEPGRAM_API_KEY else "whisper").strip().lower()
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3")
DEEPGRAM_TIMEOUT_S = int(os.getenv("DEEPGRAM_TIMEOUT_S", "300"))

# Whisper (the fallback, and the default when no Deepgram key is set). Defaults
# tuned for ACCURACY over raw speed — a mangled
# transcript ("valmera.io" -> "Valmer de laio") poisons captions AND makes the
# agent burn its whole turn lip-reading with slow vision calls. 'medium' + a
# beam search + brand hotwords fixes both. Keep WHISPER_MODEL in sync with the
# Dockerfile --build-arg (the model is baked into the image — keep it baked even
# on Deepgram, it is what the fallback runs on); set it back to
# 'small' if the worker CPU can't keep up with indexing latency.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")   # cpu | cuda
WHISPER_COMPUTE = os.getenv(
    "WHISPER_COMPUTE", "int8" if WHISPER_DEVICE == "cpu" else "float16")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
# Domain vocabulary biased into EVERY decoding window (faster-whisper >=1.0
# 'hotwords'; Deepgram's 'keyterm' — the same list feeds both). Proper nouns /
# brand terms an ASR would otherwise mis-hear.
# Comma/space separated; empty disables.
WHISPER_HOTWORDS = os.getenv("WHISPER_HOTWORDS", "Valmera, valmera.io")
# Optional priming context (style/topic) for the first window. Empty disables.
WHISPER_INITIAL_PROMPT = os.getenv("WHISPER_INITIAL_PROMPT", "")
# faster-whisper treats a window whose gzip compression ratio exceeds this as a
# repetition/looping hallucination and forces hot, unstable decodes — which
# COLLAPSES legitimately repeated takes down to a single copy. DISABLED (None)
# on purpose: ANY value here is a cap on how many times a user may repeat
# themselves, and that is unknowable — people upload RAW footage precisely
# because it has an unpredictable number of repeated takes to cut. (For scale:
# normal speech ~1.4, the same 3 sentences said 3× ~3.05, 5× ~4.99 — the library
# default of 2.4 silently eats the second take onward.)
# The failure modes are asymmetric, which is why turning it off is the safe
# direction: a hallucinated loop would land VISIBLY in the transcript and the
# user can edit it out, whereas an eaten repeat is INVISIBLE and silently breaks
# the headline feature. Hallucination is still guarded by the VAD filter (music/
# silence never reaches the decoder), no_speech_threshold, log_prob_threshold,
# and condition_on_previous_text=False (stops loops snowballing across windows).
# Set a float only if a real looping regression ever shows up.
_crt = os.getenv("WHISPER_COMPRESSION_RATIO_THRESHOLD", "none").strip().lower()
WHISPER_COMPRESSION_RATIO_THRESHOLD = (
    None if _crt in ("", "none", "off") else float(_crt))

# Quotas / limits
MAX_UPLOAD_GB = float(os.getenv("MAX_UPLOAD_GB", "2"))
MAX_DURATION_S = float(os.getenv("MAX_DURATION_S", str(3 * 3600)))

# Fetching media from a URL the user pasted (worker/url_media.py).
#
# URL_FETCH_ENABLED is the kill switch for the whole capability — set it to 0
# and the fetch_url tool disappears from the schema AND from the prompt's
# capability claims, so the agent stops offering something it cannot do.
#
# URL_FETCH_EXTRACTOR gates only the yt-dlp PAGE path (YouTube, TikTok,
# Vimeo, SoundCloud...). It is separate on purpose: downloading a direct file
# link the user owns and extracting media from a platform page are the same
# feature technically and very different legally — a platform's terms
# generally forbid the latter, and it is Valmera's IP doing the fetching. Turn
# this off and direct links keep working while pages are refused honestly.
URL_FETCH_ENABLED = os.getenv("URL_FETCH_ENABLED", "1") == "1"
URL_FETCH_EXTRACTOR = os.getenv("URL_FETCH_EXTRACTOR", "1") == "1"
# Download ceiling before we know what the file is — we cannot apply a
# per-kind limit until ffprobe has seen the bytes, so this is the largest of
# them and the real ceilings are enforced after classification.
FETCH_MAX_BYTES = int(os.getenv("FETCH_MAX_BYTES", str(500 << 20)))
# Per-kind ceilings, matching backend/storage.py's upload limits so a pasted
# link and a drag-and-drop of the same file behave identically.
FETCH_CLIP_MAX_BYTES = int(os.getenv("FETCH_CLIP_MAX_BYTES", str(500 << 20)))
FETCH_AUDIO_MAX_BYTES = int(os.getenv("FETCH_AUDIO_MAX_BYTES", str(50 << 20)))
FETCH_IMAGE_MAX_BYTES = int(os.getenv("FETCH_IMAGE_MAX_BYTES", str(10 << 20)))
# Wall-clock for one fetch. Bounded well under AGENT_TURN_TIMEOUT_S so a slow
# link fails inside the turn with an honest message instead of eating the
# whole turn and timing it out.
FETCH_TIMEOUT_S = float(os.getenv("FETCH_TIMEOUT_S", "180"))
# Path to a Netscape-format cookies.txt for the extractor, or "" for none.
#
# This is the ONLY thing that reliably gets past YouTube's "Sign in to
# confirm you're not a bot" wall. That wall is an IP-reputation check:
# Render's egress is a datacenter address, so the default web client is
# challenged on essentially every request no matter how many player clients
# we cycle through. The alternate-client chain below helps sometimes and
# cannot be relied on.
#
# Deliberately unset by default, and deliberately an operator decision:
# supplying cookies means the fetch runs as a logged-in YouTube account,
# which is a terms-of-service question (and puts that account at risk), not
# a technical one. Nothing here bypasses payment or DRM — it is the same
# public page a browser loads — but it should be switched on knowingly.
# Point it at a file on the worker's disk (e.g. a Render secret file).
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()
# Alternate player clients tried, in order, after a bot-wall failure. These
# fail FAST (the challenge comes back during extraction, long before any
# bytes are downloaded), so walking a few costs seconds, not minutes — but
# the chain is still deadline-guarded in url_media._extract_with_fallback.
YTDLP_FALLBACK_CLIENTS = [
    c.strip() for c in os.getenv(
        "YTDLP_FALLBACK_CLIENTS",
        "tv,mweb|android_vr|web_safari|tv_embedded").split("|") if c.strip()]
# Ceiling on the whole bot-wall retry chain, so a pathological case cannot
# stack full download timeouts and eat the agent turn.
FETCH_RETRY_BUDGET_S = float(os.getenv("FETCH_RETRY_BUDGET_S", "75"))
FETCH_MAX_DURATION_S = float(os.getenv("FETCH_MAX_DURATION_S", "3600"))
# Resolution cap for extracted video. A 4K source is a ~10x bigger download
# and a slower render for a clip that gets composited into a 1080p timeline.
FETCH_MAX_HEIGHT = int(os.getenv("FETCH_MAX_HEIGHT", "1080"))
# 8 (was 4): fetches are size/duration-capped individually and cleaned up
# per attempt; the constant is a runaway-loop backstop, not the real bound.
MAX_FETCHED_URLS_PER_TURN = int(os.getenv("MAX_FETCHED_URLS_PER_TURN", "8"))

# Recording a live web page as video (worker/webrecord.py) — headless
# Chromium capture of a scrolling page. WEB_RECORD_ENABLED is the kill
# switch (the tool + prompt claims vanish, same contract as URL_FETCH);
# the capability also self-disables when playwright/Chromium are not baked
# into the image (webrecord.available()). Durations: MAX_DURATION_S bounds
# what the agent may ask for; WALL_S is the hard ceiling on the whole
# browse-and-capture including page load, enforced around every step.
WEB_RECORD_ENABLED = os.getenv("WEB_RECORD_ENABLED", "1") == "1"
WEB_RECORD_MAX_DURATION_S = float(os.getenv("WEB_RECORD_MAX_DURATION_S",
                                            "30"))
WEB_RECORD_WALL_S = float(os.getenv("WEB_RECORD_WALL_S", "90"))

# Worker tuning
TMP_DIR = os.getenv("WORKER_TMP_DIR", "/tmp/valmera")
POLL_INTERVAL_S = float(os.getenv("WORKER_POLL_INTERVAL_S", "2.0"))
# The media lane runs preview + final encodes. Indexing gets its OWN lane
# (INDEX_SLOTS) so a multi-minute whisper index can never wedge interactive
# previews behind it — that starvation was the #1 cause of "I chatted and
# nothing happened" churn. Raise MEDIA_SLOTS to also stop a long final export
# from blocking previews (needs the vCPUs for concurrent ffmpeg).
MEDIA_SLOTS = int(os.getenv("WORKER_MEDIA_SLOTS", "1"))
INDEX_SLOTS = int(os.getenv("WORKER_INDEX_SLOTS", "1"))
AGENT_SLOTS = int(os.getenv("WORKER_AGENT_SLOTS", "2"))
HEARTBEAT_EVERY_S = 20

# ── Remote executor (round 38): request-based media/index compute ──────────
# The worker image runs in one of two ROLES, chosen by WORKER_ROLE:
#   "worker"   (default) — the always-on DISPATCHER. Polls the queue and owns
#              all retry/heartbeat/reaper/credit logic. Runs agent_turn LOCALLY
#              (it is network-bound — waiting on the LLM — so a GPU/many-core
#              box would sit idle). If REMOTE_EXECUTOR_URL is set it ships
#              index/preview/final to the executor over HTTP instead of
#              encoding them on this box; if unset it runs them locally exactly
#              as before (so this whole feature is off until you opt in).
#   "executor" — a STATELESS compute endpoint (main.py hands off to
#              http_server.serve()). Runs the SAME indexer/renderer code per
#              HTTP request. Deploy it on a scale-to-zero, many-core / GPU host
#              (Google Cloud Run: 8 vCPU, concurrency=1, min-instances=0) so a
#              fresh instance handles each render — no INDEX_SLOTS=1 queue, and
#              $0 while idle. It writes progress to the same Postgres, so the
#              studio's job-status polling is unchanged.
WORKER_ROLE = os.getenv("WORKER_ROLE", "worker").strip().lower()
# Dispatcher -> executor. Base URL of the Cloud Run service (no trailing path),
# e.g. https://valmera-executor-xxxx.a.run.app. Empty = run media/index locally.
REMOTE_EXECUTOR_URL = os.getenv("REMOTE_EXECUTOR_URL", "").strip().rstrip("/")
# Shared bearer secret checked by the executor (constant-time). MUST be long and
# random; the executor refuses every /run without it. Set the SAME value on both
# services. The executor still reads the job's real data from the DB — the body
# is only a job id + payload — so this guards compute/cost, not data secrecy.
REMOTE_EXECUTOR_SECRET = os.getenv("REMOTE_EXECUTOR_SECRET", "")
# Dispatcher-side HTTP timeout awaiting a remote render. Must exceed the longest
# real encode AND stay under Cloud Run's request cap (3600s). A render on 8 vCPU
# is minutes; on timeout the dispatcher requeues (renders are idempotent/cached).
REMOTE_EXECUTOR_TIMEOUT_S = int(os.getenv("REMOTE_EXECUTOR_TIMEOUT_S", "3300"))
# Port the executor's HTTP server binds (Cloud Run injects $PORT, default 8080).
EXECUTOR_PORT = int(os.getenv("PORT", "8080"))
STALE_AFTER_S = 120           # running + no heartbeat for this long => reclaimable
MAX_ATTEMPTS_MEDIA = 3        # first run + 2 retries
MAX_ATTEMPTS_AGENT = 1        # agent turns are not auto-retried (user can resend)

AGENT_MAX_ITERATIONS = 30
AGENT_TEMPERATURE = 0.2
# Wall-clock ceiling for one agent turn — a generous final backstop, not a
# leash. On expiry the loop stops, saves whatever it finished, and posts an
# honest message — never a silent "Editing…" forever.
# 720 (was 450): a real edit that ends in a preview render was landing right at
# the old ceiling — on the 1-vCPU box a preview alone is ~60-100s, so a turn
# doing genuine work plus a render had almost no margin and got cut mid-finish
# (prod job 456: 509s > 450). The efficiency fixes (add_title_card /
# add_color_screen collapse ~30 build/teardown calls to a few) and the
# in-turn time-pressure warnings at 0.55/0.8 do most of the work; this is the
# headroom so a legitimately busy turn finishes instead of being guillotined.
# The reaper does NOT fight this — a live turn's heartbeat thread keeps its
# slot healthy, so the turn timeout is the true ceiling. VIDEO_POLL_TIMEOUT_S
# (240) and FETCH_TIMEOUT_S (180) still sit safely under it. Cost of raising:
# a genuinely stuck turn holds one shared agent slot longer, so do NOT push
# this to many minutes on a 1-vCPU box.
AGENT_TURN_TIMEOUT_S = float(os.getenv("AGENT_TURN_TIMEOUT_S", "720"))
PREVIEW_WAIT_TIMEOUT_S = float(os.getenv("PREVIEW_WAIT_TIMEOUT_S", "900"))
TOOL_OUTPUT_CHAR_BUDGET = 12000   # ~3000 tokens
# Transcript tools get a far larger budget: silently dropping the tail of a
# long video's transcript is how far-apart repetitions go unseen.
TRANSCRIPT_CHAR_BUDGET = 48000    # ~12000 tokens

# Full-index-in-context (Q1): for short videos, put the ENTIRE sentence-level
# transcript + every shot caption + all keep spans directly into the per-turn
# project state so the model never has to "remember to look" — it deletes the
# whole "never bothered to check the transcript" failure class. Long videos
# fall back to the elided summary + retrieval tools. Bounded by a char cap so a
# short-but-dense video can't blow up the prompt.
FULL_INDEX_MAX_DURATION_S = float(os.getenv("FULL_INDEX_MAX_DURATION_S", "600"))
FULL_INDEX_MAX_CHARS = int(os.getenv("FULL_INDEX_MAX_CHARS", "40000"))

# A turn's budget is what the user can PAY FOR: balance + this grace. There is
# deliberately NO flat per-turn ceiling on top — the old AGENT_TURN_MAX_CREDITS
# was tuned on 16-60s clips and cut a real customer's 19-min documentary off
# mid-edit ("spend cap hit: 43.01 >= 40.0"), leaving a partial result that read
# as the agent failing. Model work scales with the footage, so a flat number
# punished long videos specifically. A free user is still bounded by their own
# small balance; a paying user gets the turn they paid for. Same 1 credit =
# $0.01 convention as billing.
AGENT_TURN_BUDGET_GRACE = float(os.getenv("AGENT_TURN_BUDGET_GRACE", "3"))
# Model prices ($/1M tokens) for the credit charge — MUST match the model in
# AGENT_MODEL or credits drift from real cost. Default = Grok 4.5 ($2 in /
# $6 out). Grok 4.5 is ~5x pricier than Qwen, so a turn costs ~5x the credits;
# set AGENT_MODEL=grok-4.1-fast + these prices lower for Qwen-like economics.
# (Must mirror db.charge_turn_credits so the in-turn cap and final charge agree.)
LLM_PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "2.0"))
LLM_PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "6.0"))
# Per-image charge (1 credit = $0.01). 0.055 tracks grok-imagine-image-quality
# (see IMAGE_GEN_MODEL); if you switch IMAGE_GEN_MODEL, set this to that tier's
# real per-image price or credits drift from cost.
IMAGE_PRICE_USD = float(os.getenv("IMAGE_PRICE_USD", "0.055"))
# AI sound effect: ElevenLabs bills a flat cost per generation — keep this in
# sync with your plan's per-sound-effect price (charged at 1 credit = $0.01).
SFX_PRICE_USD = float(os.getenv("SFX_PRICE_USD", "0.08"))
# AI video: fal bills PER SECOND (Kling 2.5 Turbo Pro ≈ $0.35 for the first 5s
# then ~$0.07/s). cost = base + max(0, seconds - base_seconds) * per_sec. Keep
# ALL THREE in sync with the fal model page for the id in VIDEO_GEN_MODEL, or
# credits drift from real cost. (Mirrored in db.charge_turn_credits via the
# per-generation cost_usd stored on the llm_calls row.)
VIDEO_BASE_PRICE_USD = float(os.getenv("VIDEO_BASE_PRICE_USD", "0.35"))
VIDEO_BASE_SECONDS = float(os.getenv("VIDEO_BASE_SECONDS", "5"))
VIDEO_PRICE_USD_PER_SEC = float(os.getenv("VIDEO_PRICE_USD_PER_SEC", "0.07"))

# The index proxy. This is an ANALYSIS + PREVIEW artifact, not a deliverable:
# shot detection and thumbnails read it, previews render from it (at ~480p),
# the studio player streams it, and finals always go back to the ORIGINAL. It
# was encoded at 720p/CRF23, which for the 720p sources people actually upload
# is a full-quality transcode wearing a proxy's name — 894s of a 19-min video's
# 47-min index, at 0.77x realtime on one vCPU, for no resolution change at all.
# 540p is >= what previews render at and what the player needs, and costs about
# half the pixels. Set PROXY_HEIGHT=720 to restore the old output.
PROXY_HEIGHT = int(os.getenv("PROXY_HEIGHT", "540"))
PROXY_PRESET = os.getenv("PROXY_PRESET", "veryfast")
PROXY_CRF = int(os.getenv("PROXY_CRF", "25"))

# Round 39 — repainting burned-in text/objects out of the source (inpaint.py).
# The clean pass decodes every frame, repaints the marked rectangles and
# re-encodes, so it costs roughly a proxy encode plus the per-frame work. It
# runs INSIDE an agent turn, so the bound that matters is AGENT_TURN_TIMEOUT_S:
# past this source length the pass would be killed mid-way and the user would
# get an error instead of an edit. Above it the agent offers the honest
# alternatives (cover with blur_region, or crop the band out of frame) rather
# than starting something that cannot finish. Raise it when the clean pass
# moves onto the media executor, where it is not racing a turn timeout.
CLEAN_MAX_SOURCE_S = float(os.getenv("CLEAN_MAX_SOURCE_S", "600"))
# Frames sampled when LOOKING for burned-in text. Detection reads the proxy and
# input-seeks, so each sample is a seek, not a decode; 28 covers a caption that
# is only on screen for part of the video without making the scan noticeable.
CLEAN_DETECT_SAMPLES = int(os.getenv("CLEAN_DETECT_SAMPLES", "28"))

PREVIEW_PRESET = os.getenv("PREVIEW_PRESET", "ultrafast")
# Final exports: veryfast/CRF20 is effectively transparent for talking-head /
# screen content and several times faster than the old medium/CRF18.
FINAL_PRESET = os.getenv("FINAL_PRESET", "veryfast")
FINAL_CRF = int(os.getenv("FINAL_CRF", "20"))

SILENCE_NOISE_DB = "-35dB"
SILENCE_MIN_S = 0.6
SCENE_THRESHOLD = float(os.getenv("SCENE_THRESHOLD", "27.0"))

# Vision-call cap during indexing: one contact sheet = 25 shots = one vision
# call. A 3-hour shot-heavy video would otherwise fire proportionally many
# calls; beyond this many sheets we sample evenly across the video and record a
# warning so the cost is bounded and the degradation is visible.
MAX_VISION_SHEETS = int(os.getenv("MAX_VISION_SHEETS", "12"))

# Render verification: after every encode, the output duration must match the
# EDL's exact expected program duration (the renderer computes it), and the
# output must not be almost entirely black. A mismatch beyond the tolerance, or
# black coverage above the ratio, retries the encode once then surfaces a real
# error instead of silently shipping a broken video.
RENDER_DURATION_TOLERANCE_S = float(
    os.getenv("RENDER_DURATION_TOLERANCE_S", "0.75"))
RENDER_DURATION_TOLERANCE_FRAC = float(
    os.getenv("RENDER_DURATION_TOLERANCE_FRAC", "0.03"))
# Deliberately high so legit dark/moody footage and short dip-to-black
# transitions never trip it — only a near-fully-black render (a real failure)
# exceeds it.
RENDER_BLACK_MAX_RATIO = float(os.getenv("RENDER_BLACK_MAX_RATIO", "0.7"))

# ---------------------------------------------------------------- end card --
# Every EXPORT closes on a branded card: black, the Valmera robot, the
# wordmark, "Edited by Valmera agent". It is a render-pipeline constant, NOT
# part of the EDL — no tool adds or removes it, and it never appears in
# program_duration, so nothing the agent places can land on top of it.
#
# FINALS ONLY, by default. Previews are program-time everywhere in the studio
# (timeline ruler, playhead, scrub mapping, the "N s program" label), so a
# preview that is 2.5s longer than its own timeline would put a permanent lie
# in the scrubber. Finals are also the only artifact that leaves the platform:
# downloads always go through a final render, previews never do. Set
# OUTRO_ON_PREVIEW=1 to show it in previews too — the renderer supports it and
# the tests cover both — but fix the studio's time base first.
OUTRO_DURATION_S = float(os.getenv("OUTRO_DURATION_S", "2.5"))
OUTRO_FADE_IN_S = 0.45
OUTRO_FADE_OUT_S = 0.35
# The program's last 0.25s is faded so music/speech does not cut dead into the
# card's silence. Skipped when the EDL already sets its own fade_out.
OUTRO_AUDIO_TAIL_FADE_S = 0.25
OUTRO_ON_PREVIEW = os.getenv("OUTRO_ON_PREVIEW", "0") == "1"
# Bumped whenever the card's LOOK changes. It is stored on every render asset
# and busts the render cache, so an existing export re-encodes with the new
# card instead of serving pre-outro bytes forever.
OUTRO_VERSION = 2      # v2: the site's white robot + premium wordmark card

# ── Free-tier watermark (round 41) ────────────────────────────────────────
# The site's robot in the top-left of the EXPORT, with "edited by valmera
# agent" sliding out beside it every few seconds and sliding back.
#
# FINAL renders only, and only for users without a paid plan. Previews are
# never marked: the watermark is what a paid plan removes, so it belongs on
# the artefact the user keeps, not on the working preview they are editing
# against. Paid users get a clean file with no watermark filter in the graph
# at all.
#
# WATERMARK_VERSION is stamped on every render asset as `wm_v` (0 = no mark)
# and busts the render cache exactly like OUTRO_VERSION, so a user who
# UPGRADES gets a clean re-encode instead of their cached marked export
# forever. Bump it whenever the mark's LOOK changes.
WATERMARK_VERSION = 1
# ON by owner's decision (the tradeoff was raised and taken deliberately).
#
# KNOWN OUTSTANDING: 44 public pages (58 occurrences) plus public/llms.txt and
# llms-full.txt still state "Valmera never puts a watermark over your footage
# — on any plan, including Free", and several RANK for "no watermark"
# queries. The subscribe page has been corrected; the /tools/* pages have NOT.
# Until they are, the marketing site advertises the opposite of what exports
# do. Set WATERMARK_ENABLED=0 to switch the mark back off in one env var if
# that needs to be undone in a hurry.
WATERMARK_ENABLED = os.getenv("WATERMARK_ENABLED", "1") == "1"
WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "edited by valmera agent")
# The site's wordmark face (frontend navbar uses Plus Jakarta Sans 800), so
# the mark on the video and the logo on the page are the same type. This is
# the font's FULL name — libass resolves it out of the bundled fonts dir the
# same way "Inter Display Black" and "Syne ExtraBold" already resolve.
WATERMARK_FONT_NAME = "Plus Jakarta Sans ExtraBold"
# Fractions of the OUTPUT frame, so the mark lands proportionate on 9:16,
# 16:9, 1:1 and 4:5 without a per-ratio asset — same principle as the card.
WATERMARK_ROBOT_H_FRAC = float(os.getenv("WATERMARK_ROBOT_H_FRAC", "0.058"))
WATERMARK_MARGIN_FRAC = float(os.getenv("WATERMARK_MARGIN_FRAC", "0.030"))
WATERMARK_TEXT_H_FRAC = float(os.getenv("WATERMARK_TEXT_H_FRAC", "0.0175"))
# Aspect of brand/robot.png (1467x2157). Pinned as a constant because the
# text's x position is computed from the robot's WIDTH, and a regenerated
# asset with a different aspect would silently overlap the two. A worker test
# asserts this matches the bundled file.
WATERMARK_ROBOT_ASPECT = 1467.0 / 2157.0
# Timing of one cycle: hidden, slide out + fade in, hold, slide back + fade
# out. Long period and short reveal on purpose — "easy to notice but not
# annoying" means it must not be reading as a banner.
WATERMARK_PERIOD_S = float(os.getenv("WATERMARK_PERIOD_S", "11.0"))
WATERMARK_SHOW_S = float(os.getenv("WATERMARK_SHOW_S", "3.4"))
WATERMARK_FADE_S = float(os.getenv("WATERMARK_FADE_S", "0.5"))
WATERMARK_SLIDE_FRAC = float(os.getenv("WATERMARK_SLIDE_FRAC", "0.014"))
WATERMARK_OPACITY = float(os.getenv("WATERMARK_OPACITY", "0.92"))

FFMPEG_TIMEOUT_S = int(os.getenv("FFMPEG_TIMEOUT_S", "5400"))
# A stalled encode stops emitting -progress lines but keeps its stdout pipe
# open, so the progress reader would block forever (this once froze the only
# media slot for hours). Kill an encode that goes this long with no progress,
# well before the full wall-clock cap above.
FFMPEG_STALL_TIMEOUT_S = int(os.getenv("FFMPEG_STALL_TIMEOUT_S", "300"))


def require_core():
    missing = [k for k, v in {
        "DATABASE_URL": DATABASE_URL,
        "S3_ENDPOINT": S3_ENDPOINT,
        "S3_ACCESS_KEY_ID": S3_ACCESS_KEY_ID,
        "S3_SECRET_ACCESS_KEY": S3_SECRET_ACCESS_KEY,
        "S3_BUCKET": S3_BUCKET,
    }.items() if not v]
    if missing:
        raise SystemExit(f"Worker cannot start — missing env: {', '.join(missing)}")
