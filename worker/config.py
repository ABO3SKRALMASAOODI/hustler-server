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

# ------------------------------------------------------- music generation --
# The agent can compose a track to order ("epic trailer, low brass, taiko
# hits, rising strings") instead of being confined to the 24 bundled CC0
# tracks. DISABLED until a key is set: with neither key present the
# generate_music tool removes itself and the agent falls back to the library
# and the user's uploads, honestly. Set ONE of these to switch it on — the
# backend is inferred from which one (see worker/music_gen.provider).
#
# Read worker/music_gen.py's docstring before enabling. The short version:
# output rights vest in the ACCOUNT HOLDER (Valmera), not automatically in
# Valmera's end users, so enabling this is a licensing posture the operator
# takes deliberately — hence a key, not a code default.
MUSIC_ELEVENLABS_API_KEY = os.getenv("MUSIC_ELEVENLABS_API_KEY", "").strip()
MUSIC_STABILITY_API_KEY = os.getenv("MUSIC_STABILITY_API_KEY", "").strip()
MUSIC_ELEVENLABS_MODEL = os.getenv("MUSIC_ELEVENLABS_MODEL", "music_v2")
# Vendor prices, for the credit charge. MUST match the vendor's current rate
# or music silently costs the user the wrong number of credits — the same
# contract LLM_PRICE_* and IMAGE_PRICE_USD carry (1 credit = $0.01).
MUSIC_ELEVENLABS_USD_PER_MIN = float(
    os.getenv("MUSIC_ELEVENLABS_USD_PER_MIN", "0.15"))
MUSIC_STABILITY_USD_PER_CALL = float(
    os.getenv("MUSIC_STABILITY_USD_PER_CALL", "0.20"))
# Generation is the slowest tool call the agent makes (~30s typical), so it
# gets a generous timeout — but well under AGENT_TURN_TIMEOUT_S so a hung
# vendor cannot eat the whole turn.
MUSIC_TIMEOUT_S = float(os.getenv("MUSIC_TIMEOUT_S", "180"))
# Cost bound per turn, mirroring MAX_GENERATED_IMAGES_PER_TURN. Not a leash on
# the agent's judgement — a stop on a retry loop burning $0.20 a go.
MAX_GENERATED_MUSIC_PER_TURN = int(
    os.getenv("MAX_GENERATED_MUSIC_PER_TURN", "3"))
# Default length when the agent does not say. Long enough to cover a typical
# short-form edit without looping, short enough not to overbill a 15s reel.
MUSIC_DEFAULT_DURATION_S = float(os.getenv("MUSIC_DEFAULT_DURATION_S", "45"))

# ------------------------------------------------------ music fetch (web) --
# "Add this song" -> the agent SEARCHES public catalogs, downloads a track and
# puts it in the edit. EXPERIMENTAL, and deliberately scoped: only catalogs
# that expose a machine-readable licence, and only works whose licence reads
# CC0 or public domain. That scope is the whole safety story — the audio ends
# up burned into a customer's exported, possibly monetized video, where a
# Content ID claim lands on THEM, weeks later, with no idea why.
#
# Provenance is recorded on every fetched asset and stated to the user,
# because a catalog licence field is UPLOADER-DECLARED, not verified. We can
# honestly say "the uploader declared this CC0, here is where it came from";
# we cannot say "this is cleared". Do not let the wording drift toward the
# second.
MUSIC_FETCH_ENABLED = os.getenv("MUSIC_FETCH_ENABLED", "1") == "1"
MUSIC_FETCH_TIMEOUT_S = float(os.getenv("MUSIC_FETCH_TIMEOUT_S", "45"))
# A 10-minute 320kbps MP3 is ~24 MB. 60 MB is generous for a long PD
# recording and still far under anything that threatens the worker's
# ephemeral disk, which is shared with every concurrent render on the box.
MUSIC_FETCH_MAX_BYTES = int(os.getenv("MUSIC_FETCH_MAX_BYTES",
                                      str(60 << 20)))
MUSIC_FETCH_MAX_RESULTS = int(os.getenv("MUSIC_FETCH_MAX_RESULTS", "6"))
# Bound on downloads per turn — these are free, but each is a multi-second
# network round trip and a retry loop should not be able to spend the turn.
MAX_FETCHED_MUSIC_PER_TURN = int(os.getenv("MAX_FETCHED_MUSIC_PER_TURN", "4"))

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
OUTRO_VERSION = 1

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
