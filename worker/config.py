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
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "grok-2-image")
# Frame/image restyling model — only used by the DashScope backend. Empty on
# the OpenAI/xAI backend (which has no image-edit endpoint).
IMAGE_EDIT_MODEL = os.getenv("IMAGE_EDIT_MODEL", "")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "")
IMAGE_TIMEOUT_S = float(os.getenv("IMAGE_TIMEOUT_S", "150"))
MAX_GENERATED_IMAGES_PER_TURN = int(
    os.getenv("MAX_GENERATED_IMAGES_PER_TURN", "4"))

# Bump whenever the index pipeline's OUTPUT changes (segmentation rules,
# VAD settings, schema...): cached indexes from older pipeline versions are
# re-built instead of served. Keep in sync with backend/routes/video.py.
PIPELINE_VERSION = int(os.getenv("PIPELINE_VERSION", "6"))

# Transcription provider. faster-whisper runs on the worker's OWN CPU — free and
# private, but 'medium' at int8 is weak exactly where this product lives (loud
# music, crowds, one word over a bar) and it is the slowest step of indexing.
# Deepgram nova-3 is materially better on that audio, returns the word-level
# timestamps the EDL needs, and takes whisper off the CPU entirely.
#   DEEPGRAM_API_KEY set  -> deepgram, with whisper as an automatic fallback
#   unset                 -> whisper, exactly as before
#   TRANSCRIBER           -> forces either side ('deepgram' | 'whisper')
# NOTE: switching providers changes the index's OUTPUT, so bump PIPELINE_VERSION
# (env, both services) at the same time to rebuild existing transcripts —
# deliberately NOT bumped in code, or every project would re-run whisper for
# nothing on installs that never set the key.
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
STALE_AFTER_S = 120           # running + no heartbeat for this long => reclaimable
MAX_ATTEMPTS_MEDIA = 3        # first run + 2 retries
MAX_ATTEMPTS_AGENT = 1        # agent turns are not auto-retried (user can resend)

AGENT_MAX_ITERATIONS = 30
AGENT_TEMPERATURE = 0.2
# Wall-clock ceiling for one agent turn — a generous final backstop, not a
# leash. On expiry the loop stops, saves whatever it finished, and posts an
# honest message — never a silent "Editing…" forever.
AGENT_TURN_TIMEOUT_S = float(os.getenv("AGENT_TURN_TIMEOUT_S", "450"))
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

# Per-turn spend cap: bound one agent turn's model cost so a 1-credit user
# can't trigger an arbitrarily expensive turn (vision + image calls) that gets
# written off. The effective budget is min(this hard ceiling, balance + grace)
# so paying users still get generous turns while free users are capped near
# what they can actually pay for. Same 1 credit = $0.01 convention as billing.
AGENT_TURN_MAX_CREDITS = float(os.getenv("AGENT_TURN_MAX_CREDITS", "40"))
AGENT_TURN_BUDGET_GRACE = float(os.getenv("AGENT_TURN_BUDGET_GRACE", "3"))
# Model prices ($/1M tokens) for the credit charge — MUST match the model in
# AGENT_MODEL or credits drift from real cost. Default = Grok 4.5 ($2 in /
# $6 out). Grok 4.5 is ~5x pricier than Qwen, so a turn costs ~5x the credits;
# set AGENT_MODEL=grok-4.1-fast + these prices lower for Qwen-like economics.
# (Must mirror db.charge_turn_credits so the in-turn cap and final charge agree.)
LLM_PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "2.0"))
LLM_PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "6.0"))
IMAGE_PRICE_USD = float(os.getenv("IMAGE_PRICE_USD", "0.05"))

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
