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

# LLM — OpenAI-compatible only. Default: OpenAI GPT-5.6 Luna
# (api.openai.com), the owner's chosen agent model since Jul 31 2026 (round
# 67). The whole stack (agent tool-calling, vision, concierge) is
# OpenAI-compatible, so pointing OPENAI_BASE_URL + OPENAI_API_KEY at any
# compatible provider is all that's needed. To run Luna you ONLY set
# OPENAI_API_KEY (an OpenAI key); the defaults below already select it.
# (For the previous stack: OPENAI_BASE_URL=https://api.deepseek.com +
# AGENT_MODEL=deepseek-v4-pro, or https://api.x.ai/v1 + grok-4.5.)
#
# LUNA TAKES IMAGES ("text, image" input modalities per OpenAI's model page,
# checked Jul 31 2026) — which is what makes the round-67 direct-sight
# look_at possible: frames go straight into the AGENT's own context instead
# of through a separate vision model. AGENT_MULTIMODAL below gates that path;
# a deployment pointed back at DeepSeek must set it to 0 (or eat one latched
# 400 — the runtime downgrade catches it either way, see llm.mark_agent_blind).
# Model id: use the exact "gpt-5.6-luna" — the bare "gpt-5.6" alias routes to
# Sol, a different (pricier) model.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-5.6-luna")
# Whether AGENT_MODEL accepts image content parts. Default 1 (Luna does).
# When on, look_at / look_at_asset hand captured frames DIRECTLY to the
# editing agent — its own eyes — instead of asking the separate VISION_*
# provider to describe them second-hand.
AGENT_MULTIMODAL = os.getenv("AGENT_MULTIMODAL", "1") == "1"
# Whether AGENT_MODEL accepts input_audio content parts — the agent's EARS
# (round 98): listen_to and render_preview hand it short clips of the actual
# sound. Same honest-off contract as images: default on, and a provider that
# rejects the part latches itself deaf for the process (llm._agent_deaf)
# while the turn continues on text + the deterministic audio QC.
AGENT_AUDIO = os.getenv("AGENT_AUDIO", "1") == "1"
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "90"))

# THE NUMBER THAT TURNS A FIVE-SECOND WAIT INTO A DEAD TURN (round 80).
#
# The OpenAI SDK retries 429 and 5xx itself, honouring the provider's own
# `retry-after` header — this is how many times. At 1, a single rate-limit
# blip ends the turn: MAX_ATTEMPTS_AGENT is 1 (an agent turn is deliberately
# not re-run by the job lane, because half an edit has already been written),
# so the SDK's retries are the ONLY retries an agent turn ever gets.
#
# What that cost, on Aug 1-2 2026: four agent turns died on a 429 whose body
# read `Limit 200000, Used 191911, Requested 25416. Please try again in
# 5.198s.` The provider named the wait, in seconds, in the error — and we
# turned it into "I'm being rate-limited by the model right now" in the user's
# chat. One customer asked three times for an edit to a 31-minute football
# match and was refused three times in eleven minutes; he did not come back.
# Nothing was wrong with the account: a token-per-minute ceiling is a function
# of the org's usage TIER, not its balance, and a single agent turn on long
# footage re-sends a growing 37k->73k-token context every few seconds, so ONE
# turn can exceed a 200k TPM ceiling entirely on its own.
#
# 5 retries with the SDK's exponential backoff rides out a burst of roughly a
# minute, which is the shape of a TPM window. It cannot mask a real outage
# (those fail every attempt and still surface) and it costs nothing when the
# provider is healthy — a retry only happens after a refusal.
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))

# Image generation runs on its OWN provider, independent of AGENT_MODEL.
# DeepSeek publishes no image-generation model (its lineup is v4-pro/v4-flash
# chat only), so when the agent moved to DeepSeek (Jul 26 2026) inheriting its
# base URL here would have failed every single generate_image call — the exact
# silent failure that already cost two multi-week outages (see MODEL ID HISTORY
# below). An unauthenticated probe of api.deepseek.com/images/generations
# returns 401 rather than 404, so their gateway auth-checks before routing:
# you cannot tell from the outside whether the route exists, and there is no
# model id to send it either way. Images therefore keep their own base URL+key:
#   IMAGE_BASE_URL  defaults to xAI, which does have the endpoint.
#   IMAGE_API_KEY   inherits OPENAI_API_KEY only when the two providers are the
#                   SAME. Cross-provider, it must be set explicitly — and until
#                   it is, image_available() is False, so generate_image is
#                   hidden from the model and its prompt paragraph is stripped
#                   (the honest-off contract). An unset key turns the capability
#                   OFF; it never turns it into a 404 the user reads as "the
#                   agent is broken".
# Two backends are supported and auto-detected from IMAGE_BASE_URL
# (see worker/llm.image_provider):
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
# The image provider's own endpoint + key (see the block comment above).
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://api.x.ai/v1")
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "") or (
    OPENAI_API_KEY if IMAGE_BASE_URL == OPENAI_BASE_URL else "")

# ── Vision (look_at / look_at_asset / contact-sheet captions / self-check) ───
# Its OWN provider, for the same reason images have one: the chat provider may
# have no multimodal surface at all. DeepSeek does not — it 400s on an
# image_url part (see the block at the top), which blinded the agent for hours
# on Jul 26 2026 because vision silently inherited AGENT_MODEL.
#
# Defaults to OpenAI gpt-5.6-luna (round 67) — the agent model is multimodal
# now, so the indexing contact sheets, the preview self-check and any legacy
# vision call run on the same provider and key as the agent. The key follows
# the SAME inheritance rule as IMAGE_API_KEY: taken from whichever configured
# provider shares this base URL, never handed across providers (a DeepSeek
# key sent to xAI is a 401 on every call) — which on the Luna default means
# it lights up from OPENAI_API_KEY with no extra config. With no key for it,
# vision_available() is False and every vision tool says so plainly instead
# of failing — the honest-off contract. Empty VISION_MODEL disables vision
# the same graceful way.
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "https://api.openai.com/v1")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-5.6-luna")
VISION_API_KEY = (
    os.getenv("VISION_API_KEY", "")
    or (OPENAI_API_KEY if VISION_BASE_URL == OPENAI_BASE_URL else "")
    or (IMAGE_API_KEY if VISION_BASE_URL == IMAGE_BASE_URL else ""))

# ── The optional middle model tier (OFF by default) ──────────────────────────
#
# TWO MODEL TIERS SHIP, NOT THREE (round 49):
#
#   free / Creator 'ai' / Pro 'ai_pro'   AGENT_MODEL  — DeepSeek V4 Pro
#   Frontier 'ai_max'                    FRONTIER_*   — the frontier model
#
# Pro is a VOLUME break, not a model break: the same editor and the same model
# as Creator, with double the credits. That is what its card says ("more room"),
# and the two have to agree — a badge claiming a better model on a tier running
# the identical one is exactly what customers find out.
#
# This lane exists so Pro CAN become a model tier with one env var
# (PAID_PLANS=ai_pro) and no code change. It ships OFF because the only model
# worth promoting Pro to is the same one Frontier runs, and a middle tier
# indistinguishable from the top tier is worse than no middle tier: it makes the
# $100 card's entire argument false. Give Frontier something genuinely stronger
# FIRST, then promote Pro into the gap that opens up.
#
# A trial always runs its OWN plan's model. A trial previewing a model the
# customer stops getting the moment they pay is the bait-and-switch, not the fix
# for it.
#
# Credits stay correct across any split automatically: the charge prices each
# llm_calls row from its own `model` column (model_prices.py), which is the
# entire reason that indirection exists. Whatever PAID_AGENT_MODEL is set to
# MUST be listed in model_prices.MODEL_PRICES, or it silently bills at the
# LLM_PRICE_* fallback.
PAID_BASE_URL = os.getenv("PAID_BASE_URL", "https://api.x.ai/v1").strip()
PAID_AGENT_MODEL = os.getenv("PAID_AGENT_MODEL", "grok-4.5").strip()
PAID_API_KEY = (
    os.getenv("PAID_API_KEY", "").strip()
    or (VISION_API_KEY if PAID_BASE_URL == VISION_BASE_URL else "")
    or (IMAGE_API_KEY if PAID_BASE_URL == IMAGE_BASE_URL else "")
    or (OPENAI_API_KEY if PAID_BASE_URL == OPENAI_BASE_URL else ""))

# EMPTY = this lane is off and every non-Frontier plan runs AGENT_MODEL. The
# provider above stays configured so switching it on is one variable, not four.
PAID_PLANS = set(
    p.strip() for p in os.getenv("PAID_PLANS", "").split(",") if p.strip())

# ── The FIRST-TURN lane (round 81) — an A/B lever, not a tier ────────────────
#
# The first agent turn a FREE account ever runs is where retention lives or
# dies (project 319: two turns, then gone forever), and it is the one turn
# where model quality is marketing spend rather than margin. Setting
# FIRST_TURN_AGENT_MODEL routes exactly that turn — free accounts only, first
# 'agent_turn' job ever, everything after runs AGENT_MODEL as normal — to a
# stronger model. OFF by default; measure a cohort before believing in it
# (boosted turns are identifiable in llm_calls by this model id, which is the
# whole readout). Subscribers never hit it: their plan's lane already answers,
# and a paying customer's model is a promise, not an experiment.
#
# Defaults make the cheap case one variable: BASE_URL defaults to the chat
# provider, so naming a bigger model on the SAME provider needs no key. A
# different provider needs FIRST_TURN_BASE_URL + FIRST_TURN_API_KEY (keys
# never cross providers — same rule as every lane above). Whatever model this
# names MUST be in model_prices.MODEL_PRICES or it bills at the LLM_PRICE_*
# fallback.
FIRST_TURN_BASE_URL = os.getenv("FIRST_TURN_BASE_URL", OPENAI_BASE_URL).strip()
FIRST_TURN_AGENT_MODEL = os.getenv("FIRST_TURN_AGENT_MODEL", "").strip()
FIRST_TURN_API_KEY = (
    os.getenv("FIRST_TURN_API_KEY", "").strip()
    or (OPENAI_API_KEY if FIRST_TURN_BASE_URL == OPENAI_BASE_URL else "")
    or (VISION_API_KEY if FIRST_TURN_BASE_URL == VISION_BASE_URL else "")
    or (IMAGE_API_KEY if FIRST_TURN_BASE_URL == IMAGE_BASE_URL else ""))
# ── The model the FRONTIER plan gets ─────────────────────────────────────────
#
# 'ai_max' ($100/mo) is sold on the model, not on the credit count: every agent
# turn AND every look at the footage runs on the frontier provider. That is the
# only plan where the model is the product, so unlike PAID_* above it is not a
# cost optimisation with a fallback — it is a promise attached to a price.
#
# Which is why the defaults here are NOT empty. PAID_* defaults off because
# shipping it must be a no-op; this defaults ON (xAI + grok-4.5) because a
# Frontier subscriber silently served DeepSeek is a refund, and because the key
# is usually already present: VISION_API_KEY has to be an xAI key for vision to
# work at all (no DeepSeek V4 tier accepts images), and it inherits from there.
#
# The inheritance follows the same rule as IMAGE_API_KEY and VISION_API_KEY —
# a key is only ever taken from a provider on the SAME base URL, never handed
# across providers. With no key on any of them, llm.frontier_available() is
# False and Frontier falls back to the PAID_* tier and then to AGENT_MODEL, and
# says so loudly in the log on every turn rather than 401ing the customer.
FRONTIER_BASE_URL = os.getenv("FRONTIER_BASE_URL", "https://api.x.ai/v1").strip()
FRONTIER_AGENT_MODEL = os.getenv("FRONTIER_AGENT_MODEL", "grok-4.5").strip()
FRONTIER_VISION_MODEL = os.getenv("FRONTIER_VISION_MODEL", "grok-4.5").strip()
FRONTIER_API_KEY = (
    os.getenv("FRONTIER_API_KEY", "").strip()
    or (VISION_API_KEY if FRONTIER_BASE_URL == VISION_BASE_URL else "")
    or (IMAGE_API_KEY if FRONTIER_BASE_URL == IMAGE_BASE_URL else "")
    or (PAID_API_KEY if FRONTIER_BASE_URL == PAID_BASE_URL else "")
    or (OPENAI_API_KEY if FRONTIER_BASE_URL == OPENAI_BASE_URL else ""))

# Plans that route to the frontier provider. A set, so adding a higher tier
# later is one edit and no new branch.
FRONTIER_PLANS = {"ai_max"}

# reasoning_effort for the agent's tool-dispatch steps.
#
# grok-4.5 defaults to "high" and has never been told otherwise: on a measured
# day that was 521.5K reasoning tokens, ~70% of all output and 16% of the bill.
# But it is not waste everywhere — iteration ONE is where the model reads the
# project state and plans the edit, and that is precisely the thinking being
# paid for. Iterations 2+ are mostly "which tool, which arguments", which xAI's
# own guidance puts under "low".
#
# So this applies from the SECOND iteration on, never the first.
#
# Round 63 set this to "low" to stop paying for deliberation on steps that
# are pure tool dispatch: 402 of the average call's 646 output tokens were
# reasoning, mostly on iterations that just fill in arguments.
#
# ROUND 91b RAISES IT TO "high", because that arithmetic turned out to be a
# rounding error and the thing it was saving on is the product. Measured on
# gpt-5.6-luna (0.20 in / 1.20 out per 1M, reasoning FOLDED into
# completion_tokens): job 2603's whole four-call turn spent 775 output tokens,
# 573 of them reasoning — $0.0009. The same turn re-sent ~28,000 INPUT tokens
# per call, about $0.022. Input outweighs all the thinking by ~25x, so
# throttling reasoning was optimising the wrong side of the bill.
#
# And the other side of the ledger is what it costs to think too little: the
# same request that a non-reasoning turn spent 923 seconds and five inpaint
# passes failing at (job 2599) was answered correctly in 37 seconds once
# reasoning came back (job 2603). Deliberation is cheaper than the work it
# prevents.
#
# It does cost WALL CLOCK — a high-effort call thinks for longer — so if turns
# start feeling slow again, this is the first knob to turn back down, not the
# last. AGENT_REASONING_EFFORT=high (or medium) on Render, no deploy needed;
# "" sends no field at all and restores each provider's own default.
#
# Round 58 made the field safe to send blind: a provider that rejects it
# retries once without it and latches per model, matched narrowly on the field
# name plus an unknown-parameter phrase, so a provider that has never heard of
# it costs one failed call per process, ever.
#
# ROUND 97 RAISES IT TO "max". gpt-5.6 accepts none|low|medium|high|xhigh|max
# on /v1/responses (OpenAI reasoning guide, checked Aug 8 2026; omitting the
# field defaults to medium). The Aug 6-8 cohort showed what missing
# deliberation costs: the steps that fell back to effort='none' are where the
# spirals live (one turn repeated an identical no-op cut_range TEN times in a
# row — pure dispatch, zero thought, ~2.5 min of a user's clock). Thinking
# harder is cheap next to re-doing work: input tokens already outweigh
# reasoning ~25x per step (round 91b), and reasoning output is billed only
# when generated.
#
# Three rails make "max" safe where "high" was already fraying:
#  - AGENT_LANE_TIMEOUT_S below — a max-effort call can think past the 90s
#    LLM_TIMEOUT_S, and every timeout used to demote that step to the chat
#    lane at effort='none' (the 'high,none' mixing on every long turn in
#    llm_calls Aug 6-8).
#  - llm._from_responses returns an empty 'length' completion when reasoning
#    eats the whole token budget, so the loop's doubling retry runs IN the
#    lane instead of falling back to no reasoning at all.
#  - llm.looks_like_responses_unsupported no longer latches the lane dead on
#    a 400 about the effort VALUE, so a provider trimming its enum degrades
#    one step, not the process.
AGENT_REASONING_EFFORT = os.getenv("AGENT_REASONING_EFFORT", "max").strip()
# The first call plans at the model's full reasoning effort. Later calls only
# dispatch that plan and default to medium: projects 480-484 spent most of
# their time and reasoning tokens deciding the next already-obvious tool at
# max effort. The env can still raise or lower dispatch effort independently.
AGENT_REASONING_EFFORT_DISPATCH = os.getenv(
    "AGENT_REASONING_EFFORT_DISPATCH", "medium").strip() or "medium"
# Wall-clock for ONE responses-lane call. LLM_TIMEOUT_S (90) is sized for
# dispatch-grade calls; at effort max the model may THINK for minutes before
# its first output token, and a timeout here does not fail the turn — it
# silently reruns the step on chat/completions where this model answers with
# effort='none'. So the lane gets its own, thinking-sized budget. Kept well
# under AGENT_TURN_TIMEOUT_S (900) so one hung call cannot eat a turn.
AGENT_LANE_TIMEOUT_S = float(os.getenv("AGENT_LANE_TIMEOUT_S", "240"))
# Ask /v1/responses when — and only when — the model has already refused to
# reason alongside function tools on /v1/chat/completions. That refusal is why
# the agent ran 875 straight calls with ZERO reasoning tokens from Jul 31 2026
# (see llm.responses_available). Set AGENT_RESPONSES_LANE=0 to turn the lane
# off from Render without a deploy; every failure inside it already falls back
# to the chat/completions call that runs today, so off and broken are the same
# behaviour, not different ones.
AGENT_RESPONSES_LANE = os.getenv("AGENT_RESPONSES_LANE", "1") == "1"
# OpenAI serving tier for the agent's calls (round 94). There is NO fast
# MODEL variant of the gpt-5.6 family — luna/terra/sol differ by
# intelligence-per-dollar, not speed — the "fast version" OpenAI sells is
# PRIORITY PROCESSING: the same model served from a faster pool, requested
# per call with service_tier='priority' at exactly 2x the standard price
# (in/cached/out — pricing page, checked Aug 7 2026). Measured on our key,
# /v1/responses, gpt-5.6-luna, 1400-token generations off-peak: median 113
# -> 138 tok/s (+22%); the documented bigger win is queue/latency
# CONSISTENCY at peak hours. Empty = default tier (off).
#
# FLIP THE PRICES WITH THE TIER or the credit charge silently halves the
# LLM margin: setting OPENAI_SERVICE_TIER=priority on Render requires
# LLM_PRICE_IN_PER_M=0.40, LLM_PRICE_CACHED_IN_PER_M=0.04,
# LLM_PRICE_OUT_PER_M=2.40 beside it (worker AND any other service reading
# them) — users then burn credits ~2x faster on the LLM share of a turn,
# which is the honest cost of the faster pool.
OPENAI_SERVICE_TIER = os.getenv("OPENAI_SERVICE_TIER", "").strip()
# Vision (look_at) is the slowest thing the agent does, so it gets a MORE
# generous per-call timeout than the text agent (grok multimodal latency is
# spiky) — retries stay at the client default. The agent isn't capped on how
# many looks it may take; the accurate transcript (so it stops lip-reading)
# plus the longer turn wall are what keep vision from running away.
VISION_TIMEOUT_S = float(os.getenv("VISION_TIMEOUT_S", "120"))
# 8 (was 4): the real bound is the user's credit budget (_gen_budget_reject
# prices every image before spending); this stays only as a backstop against
# a runaway generation loop.
MAX_GENERATED_IMAGES_PER_TURN = int(
    os.getenv("MAX_GENERATED_IMAGES_PER_TURN", "8"))

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
# Upload codec for the transcription request. The index wav is 16k mono PCM
# (~1.9 MB per minute); FLAC is lossless and roughly halves the bytes on the
# wire, so a long video starts transcribing seconds sooner. Deepgram decodes
# FLAC natively at no accuracy cost. 'wav' turns the conversion off; any
# conversion failure silently falls back to sending the wav itself.
DEEPGRAM_UPLOAD = os.getenv("DEEPGRAM_UPLOAD", "flac").strip().lower()

# ── Live music search (round 98) ─────────────────────────────────────────────
# The bundled CC0 pack is retired from the agent's surface; search_music /
# fetch_music find commercial-use tracks online at request time instead
# (music_search.py). Openverse needs no key; a JAMENDO_CLIENT_ID upgrades
# results to a real music catalog ordered by this month's popularity.
MUSIC_SEARCH_ENABLED = os.getenv("MUSIC_SEARCH_ENABLED", "1") == "1"
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "").strip()
MUSIC_FETCH_MAX_MB = int(os.getenv("MUSIC_FETCH_MAX_MB", "30"))

# Round 69 — TWO FLAGS THAT WERE ALWAYS FREE AND ALWAYS OFF.
#
# DIARIZATION labels every word with a speaker index. Without it "keep only
# the parts where the interviewer talks", "cut to the other person" and
# "trim my co-host's rambling" have no data behind them at all; the agent
# could read what was said and never who said it.
#
# FILLER WORDS are the sharper one. nova-3 DROPS "um"/"uh" from the
# transcript unless asked for them, and remove_filler_words cuts the video
# using word TIMESTAMPS — so with no filler words in the index there are no
# spans to cut. Measured on prod before this shipped: ZERO filler tokens
# across 12,263 transcribed words in 85 indexes. Not "few" — zero. The tool
# has therefore answered "No filler words were found in the transcript, so
# nothing was removed" for every video ever uploaded, while 5 users asked
# for exactly that. Turning this on is what makes an already-shipped tool
# start working.
#
# Both are included in nova-3's per-minute price — no extra billing, one
# query parameter each. Deepgram's TEXT-level intelligence (sentiment,
# topics, intents, summarize) is billed PER FEATURE on top, so it is a
# separate opt-in below rather than part of this.
DEEPGRAM_DIARIZE = os.getenv("DEEPGRAM_DIARIZE", "1") == "1"
DEEPGRAM_FILLER_WORDS = os.getenv("DEEPGRAM_FILLER_WORDS", "1") == "1"
# Billed extra, and text-level rather than acoustic — the vocal-stress
# measurement in perception.py is a better emphasis signal for an editor
# than transcript sentiment. Off by default; set to a comma list of
# 'sentiment,topics,intents' to turn on.
DEEPGRAM_INTELLIGENCE = tuple(
    f.strip() for f in os.getenv("DEEPGRAM_INTELLIGENCE", "").split(",")
    if f.strip() in ("sentiment", "topics", "intents", "summarize"))

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
# 'hotwords'; Deepgram's 'keyterm' — the same list feeds both).
# Comma/space separated; empty disables.
#
# EMPTY BY DEFAULT, and the old default is the reason: it was "Valmera,
# valmera.io" — our own brand biased into every CUSTOMER's transcript. On Jul
# 31 2026 a real customer's 27s clip of a woman speaking came back from
# Deepgram as language "Indonesian" with EXACTLY ONE transcribed word:
# "Valmera." — the keyterm hallucinated itself as her entire transcript, and
# captions are generated from the transcript, so her video got a "Valmera."
# caption she never said (index 201, project 293). Brand hotwords belong on
# the deployment that transcribes OUR demo videos, set explicitly via env —
# never in the default path a customer's footage takes.
WHISPER_HOTWORDS = os.getenv("WHISPER_HOTWORDS", "")
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
# Mirrors backend/storage.MAX_UPLOAD_GB — the backend is what enforces it at
# presign; this copy exists for the worker's own sanity checks. Raised from 2,
# which contradicted MAX_DURATION_S below: 2 GiB over 3 hours is 1.6 Mbps.
MAX_UPLOAD_GB = float(os.getenv("MAX_UPLOAD_GB", "14"))
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
# Finding a link for a NAMED song (worker/song_find.py): yt-dlp web search
# feeding fetch_url. FIND_SONG_ENABLED is the deployment switch;
# FIND_SONG_USER_IDS narrows it to a CSV of account ids ("" = every user) —
# set it to just the admin id to keep the capability personal without a
# redeploy. The tool also vanishes whenever the fetch/extractor path it
# hands links to is off.
FIND_SONG_ENABLED = os.getenv("FIND_SONG_ENABLED", "1") == "1"
FIND_SONG_USER_IDS = os.getenv("FIND_SONG_USER_IDS", "").strip()
# Live sound-effect search (worker/sfx_search.py): real recorded sounds
# from the open web (Openverse fronting Freesound), replacing BOTH the
# deleted bundled pack and AI sound generation.
SFX_SEARCH_ENABLED = os.getenv("SFX_SEARCH_ENABLED", "1") == "1"
SFX_FETCH_MAX_MB = int(os.getenv("SFX_FETCH_MAX_MB", "20"))
# Stock/topical b-roll search (worker/stock.py): photos are keyless via
# Openverse (Wikimedia/Flickr — real people, companies, events); video
# stock needs PEXELS_API_KEY/PIXABAY_API_KEY. Topical web VIDEO is
# find_footage (below).
STOCK_SEARCH_ENABLED = os.getenv("STOCK_SEARCH_ENABLED", "1") == "1"
# Finding real footage for a NAMED topic (worker/song_find.py,
# search_footage): the web's video search feeding fetch_url as_kind='clip'
# — the b-roll editors actually pull. Same allowlist semantics as
# find_song ("" = every user).
FIND_FOOTAGE_ENABLED = os.getenv("FIND_FOOTAGE_ENABLED", "1") == "1"
FIND_FOOTAGE_USER_IDS = os.getenv("FIND_FOOTAGE_USER_IDS", "").strip()
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
# Operator cookies for the extractor — see worker/ytaccess.py for the whole
# story (why they exist, why they expire, and the five delivery doors).
#
# Supplying cookies means the fetch runs as a logged-in YouTube account,
# which is a terms-of-service question (and puts that account at risk), not
# a technical one. Nothing here bypasses payment or DRM — it is the same
# public page a browser loads — but it should be switched on knowingly.
#
# YTDLP_COOKIES_FILE: a path (Render Secret File) — or, because that is
# what actually happened, the jar CONTENT pasted where the path belongs;
# ytaccess accepts both. YTDLP_COOKIES: content, no path pretense.
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()
YTDLP_COOKIES = os.getenv("YTDLP_COOKIES", "")
# Where Render mounts Secret Files; scanned for a jar-shaped file when the
# path vars point nowhere. "" disables the scan.
YTDLP_SECRETS_DIR = os.getenv("YTDLP_SECRETS_DIR", "/etc/secrets").strip()
# app_kv key holding a jar delivered over psql ("" disables) — the door for
# an operator with database hands but no dashboard.
YTDLP_COOKIES_KV_KEY = os.getenv("YTDLP_COOKIES_KV_KEY",
                                 "ytdlp_cookies").strip()
# The bgutil PO-token script baked by the Dockerfile's POT layer. Tokens are
# the anonymous, nothing-to-rotate way to look legitimate from a datacenter
# IP; ytaccess only engages this when the directory really exists, so dev
# machines and POT=0 builds stay silently anonymous. "" disables outright.
YTDLP_POT_SERVER_HOME = os.getenv("YTDLP_POT_SERVER_HOME",
                                  "/opt/bgutil-pot/server").strip()
# Boot-time fetchability probe (worker/ytaccess.probe): one --simulate
# extraction from this box, verdict to the log and the app_kv row
# 'ytdlp_probe'. The only way to see "does YouTube serve THIS IP" without
# dashboard access. "0" skips it.
YTDLP_BOOT_PROBE = os.getenv("YTDLP_BOOT_PROBE", "1").strip()
YTDLP_PROBE_URL = os.getenv(
    "YTDLP_PROBE_URL", "https://www.youtube.com/watch?v=dQw4w9WgXcQ").strip()
# The other way past the bot wall: route ONLY the extractor through a proxy
# whose exit is a residential/mobile address (any scheme yt-dlp accepts,
# e.g. http://user:pass@host:port). No account involved, so nothing can be
# banned — the trade is a paid proxy vendor instead. Empty = direct. When
# both this and cookies are set, both apply (cookies ride the proxy).
YTDLP_PROXY = os.getenv("YTDLP_PROXY", "").strip()
# yt-dlp remote components for the EJS challenge solver ("" disables).
# "ejs:github" fetches yt-dlp's own solver script; the Dockerfile ships
# deno to run it. Required for cookie-mode fetches (see YTDLP_COOKIES_FILE
# above) — the logged-in client path gates formats behind a JS challenge.
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS",
                                    "ejs:github").strip()
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
# Runaway backstop for stock b-roll, same contract as the URL cap above: each
# download is individually byte- and time-bounded, so this only stops a loop
# that would fill the worker's ephemeral disk. Taste, not capacity, is the
# real limit — 1-3 cutaways a minute beat wall-to-wall b-roll.
MAX_STOCK_PER_TURN = int(os.getenv("MAX_STOCK_PER_TURN", "6"))

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

# ── Driven demos (round 45) ──────────────────────────────────────────────
# A demo WALKTHROUGH — the browser clicks, types and scrolls through the
# product while a drawn cursor moves on screen — is a longer shot than the
# scroll-pan, and its length is decided by the steps rather than asked for
# up front. So it gets its own, larger ceilings. Both are still bounded:
# every step is individually deadline-checked against WALL_S, which is what
# actually stops a hung page from holding an agent slot.
WEB_DEMO_MAX_DURATION_S = float(os.getenv("WEB_DEMO_MAX_DURATION_S", "75"))
WEB_DEMO_WALL_S = float(os.getenv("WEB_DEMO_WALL_S", "180"))
# A runaway backstop on the script itself, not a taste limit: 24 steps is
# far more than any watchable demo, and the duration ceiling binds first.
WEB_DEMO_MAX_STEPS = int(os.getenv("WEB_DEMO_MAX_STEPS", "24"))
# Typing is recorded at human speed or the field fills instantly and reads
# as a paste. Milliseconds per character.
WEB_DEMO_TYPE_DELAY_MS = int(os.getenv("WEB_DEMO_TYPE_DELAY_MS", "55"))

# Worker tuning
TMP_DIR = os.getenv("WORKER_TMP_DIR", "/tmp/valmera")

# Scratch space a job is assumed to need, as a multiple of its SOURCE size:
# the original, plus the artifacts written beside it (proxy, wav, thumbnails,
# or the render output). 2.2x comfortably covers a final (source + an H.264
# export of a cut-down programme) and an index (source + a 540p proxy + a 16k
# mono wav). Used by storage.check_workdir_capacity to refuse a job that cannot
# fit BEFORE it gets OOM-killed — on Cloud Run TMP_DIR is RAM, so running out
# kills the container rather than raising, and the job's only epitaph is
# "Worker died and retries are exhausted".
WORKDIR_HEADROOM = float(os.getenv("WORKDIR_HEADROOM", "2.2"))
# The executor source cache is an optimisation, never a reservation on the
# whole scratch disk.  A six-hour age limit alone let one 11.5 GB source sit
# beside the next job: the second 11.5 GB final then saw only 20 GB free and
# was correctly refused by the 2.2x capacity guard.  Keep normal phone/video
# sources hot, but make unusually large files one-job downloads and cap the
# aggregate cache so an idle instance is always ready for another large job.
SOURCE_CACHE_MAX_ITEM_BYTES = int(float(os.getenv(
    "SOURCE_CACHE_MAX_ITEM_GB", "4")) * 1024 ** 3)
SOURCE_CACHE_MAX_BYTES = int(float(os.getenv(
    "SOURCE_CACHE_MAX_GB", "8")) * 1024 ** 3)
POLL_INTERVAL_S = float(os.getenv("WORKER_POLL_INTERVAL_S", "2.0"))
# The media lane runs preview + final encodes. Indexing gets its OWN lane
# (INDEX_SLOTS) so a multi-minute whisper index can never wedge interactive
# previews behind it — that starvation was the #1 cause of "I chatted and
# nothing happened" churn.
#
# Round 91: with a REMOTE executor the slots are pure HTTP waits — the ffmpeg
# runs on Cloud Run, one instance per job, and the executor scales to
# max-instances on its own. One media slot therefore serialized every render
# on the PLATFORM behind a queue the compute never needed: two users editing
# at once meant the second stared at a spinner for the whole of the first
# user's render (preview queue_wait p50 was fine and the tail was 250s+).
# Defaults: 3 media + 2 index remote (executor max-instances is 5, and the
# in-turn synchronous calls — frames/clean/track/capture — share that
# ceiling, so media+index must not be able to occupy all of it), 1 + 1 local
# where the encodes genuinely contend for this box's own CPU. The media/index
# lanes also poll faster remotely — a claim is one indexed SKIP LOCKED query,
# and 2s of claim latency was pure dead time on every render.
_REMOTE_EXEC = bool(os.getenv("REMOTE_EXECUTOR_URL", "").strip())
MEDIA_SLOTS = int(os.getenv("WORKER_MEDIA_SLOTS", "3" if _REMOTE_EXEC else "1"))
INDEX_SLOTS = int(os.getenv("WORKER_INDEX_SLOTS", "2" if _REMOTE_EXEC else "1"))
MEDIA_POLL_INTERVAL_S = float(os.getenv(
    "WORKER_MEDIA_POLL_INTERVAL_S", "0.5" if _REMOTE_EXEC else "2.0"))
# Round 100: 4, up from 2, and claim_job now serializes agent work PER
# PROJECT (db.claim_job) so extra slots can never race two turns onto one
# timeline. An agent turn is network waiting — the slots are nearly free —
# and on Aug 8 a real user's queued message sat 14 minutes behind another
# user's long turn while the box did nothing.  The live Render service still
# had the old WORKER_AGENT_SLOTS=2 override after this default changed, and a
# production overlap audit proved it was therefore still capped at exactly
# two.
#
# Round 101: the max(4, env) floor is GONE. 19 "Worker died and retries are
# exhausted" turns in the 3 days after slots went to 4 — the box OOMs under
# concurrent turns' native work (audio decode, tile assembly, yt-dlp), and a
# floor that forbids the operator from trading latency for survival turns a
# tuning knob into an outage. Default stays 4; the env now wins in BOTH
# directions. (Heartbeats log RSS so the next death names its number.)
AGENT_SLOTS = max(1, int(os.getenv("WORKER_AGENT_SLOTS", "4")))
# The agent lane's claim poll is the gap between "user hit send" and "the
# turn starts" — the first latency anyone feels, on every single message.
# 0.5s, like the MCP lane: the claim is one indexed SKIP LOCKED query.
AGENT_POLL_INTERVAL_S = float(os.getenv("WORKER_AGENT_POLL_INTERVAL_S", "0.5"))
HEARTBEAT_EVERY_S = 20

# ── MCP lane (round 49) ──────────────────────────────────────────────────────
# One `mcp_tool` row = one tool call from an OUTSIDE model (Claude in the
# user's own Claude Code session). Its own lane for two reasons: an MCP call
# must never wait behind a customer's multi-minute agent turn, and it must
# never take a slot away from one either.
#
# The lane polls far faster than the others because a human is watching: at the
# shared 2s interval, a 40-call editing session would spend over a minute
# doing nothing but waiting for the next poll. The query is a single indexed
# SKIP LOCKED update, so 4/s costs nothing.
MCP_SLOTS = int(os.getenv("WORKER_MCP_SLOTS", "2"))
MCP_POLL_INTERVAL_S = float(os.getenv("WORKER_MCP_POLL_INTERVAL_S", "0.25"))
# An MCP call is never retried. Tools are not idempotent — a re-run of
# add_music adds a SECOND track — and the caller is a live model that can
# simply try again with what it knows.
MAX_ATTEMPTS_MCP = 1
# A project's ToolContext (downloaded proxy, parsed index, cached perception)
# is kept between calls so a session does not re-download the proxy for every
# look_at, and swept this long after its last use.
MCP_SESSION_TTL_S = float(os.getenv("WORKER_MCP_SESSION_TTL_S", "1800"))
# How many projects may hold a live context at once — each one owns a work dir
# on the same small disk the renders use.
MCP_MAX_SESSIONS = int(os.getenv("WORKER_MCP_MAX_SESSIONS", "3"))

# ── Handing the model the VIDEO itself (round 83, mcp_media.py) ────────────
# Some models on the other end of MCP can watch video, not just read our
# description of it. watch_video hands them the real file. Re-encoding is the
# EXCEPTION — the proxy and the preview render are already small H.264 with
# audio, so the normal answer is a presigned URL to the object that already
# exists and these numbers never come into it.
#
# The ceiling a re-encode aims at. Never UP-scales: it is a cap, and a 540p
# proxy stays 540p.
MCP_VIDEO_HEIGHT = int(os.getenv("MCP_VIDEO_HEIGHT", "540"))
MCP_VIDEO_CRF = int(os.getenv("MCP_VIDEO_CRF", "28"))
MCP_VIDEO_PRESET = os.getenv("MCP_VIDEO_PRESET", "veryfast")
MCP_VIDEO_AUDIO_KBPS = int(os.getenv("MCP_VIDEO_AUDIO_KBPS", "96"))
# Frame rate is kept as the source has it, capped here. Dropping frames is the
# last thing to trade away: a video model reading motion needs them, and the
# bitrate ladder in mcp_media already spends the byte budget first.
MCP_VIDEO_FPS_CAP = float(os.getenv("MCP_VIDEO_FPS_CAP", "30"))
# Longest window this will RE-ENCODE in one call. Nothing to do with what can
# be watched — the untouched-object path has no limit at all — but a 40-minute
# transcode on the dispatcher holds an MCP lane for the whole run. Past this
# the answer names the number and asks for start/end.
MCP_VIDEO_MAX_ENCODE_S = float(os.getenv("MCP_VIDEO_MAX_ENCODE_S", "1800"))
# What the DEFAULT (delivery="auto") may hand over as a plain URL with no
# re-encode. Above this, a link is a bad answer nobody asked for — the caller
# has to download gigabytes before it can look — so it gets a shrunk copy
# instead. delivery="url" is the caller asking, and is not bound by this.
MCP_VIDEO_URL_MAX_MB = float(os.getenv("MCP_VIDEO_URL_MAX_MB", "512"))
# THE PROGRAM'S SOUND, handed over with its pictures (round 83f). A model
# asked "can you hear the music" and had to download the MP4 and build a
# SPECTROGRAM to answer, because the reply carried frames and no audio — and
# the reply text said "H.264 + AAC", which invited it to claim it had heard
# something it never received. MCP has an audio content type; we simply were
# not using it.
#
# mp3 because audio/mpeg is the one audio mime a client that takes audio at
# all will accept. Mono, and the bitrate falls out of the length: sound is
# CHEAP next to video — 30s at 64 kbps is 230 KB, where the same 30s of video
# was 2.9 MB — which is the whole reason this can ride in the reply when the
# video cannot.
MCP_AUDIO_OUT = os.getenv("MCP_AUDIO_OUT", "1").strip().lower() \
    not in ("0", "false", "no", "off")
#
# The numbers are chosen against the ONE thing that has actually gone wrong
# here: a payload big enough to end a session if the client stringifies it
# instead of decoding it. 48 kbps mono is comfortably enough to hear what the
# music is doing, and puts 30 seconds at ~180 KB — roughly 45k characters of
# base64 in the worst case, against the FOUR MILLION that killed the video
# blob. Raise them only if the client is known to take audio content.
MCP_AUDIO_MAX_KB = float(os.getenv("MCP_AUDIO_MAX_KB", "256"))
# Below this the music stops being music. A window longer than the budget can
# pay for at this rate gets NO audio and a sentence saying to narrow it —
# never a silently truncated or unlistenable track. At the floor the longest
# window that still gets sound is ~85s.
MCP_AUDIO_MIN_KBPS = int(os.getenv("MCP_AUDIO_MIN_KBPS", "24"))
MCP_AUDIO_MAX_KBPS = int(os.getenv("MCP_AUDIO_MAX_KBPS", "48"))

# How many pictures one MCP tool call may hand back. A look tool assembles
# its frames into ONE labeled sheet, so this is a runaway guard, not the
# frame count — that is MCP_WATCH_FRAMES.
MCP_MAX_IMAGES = int(os.getenv("MCP_MAX_IMAGES", "4"))
# Frames watch_video lays out across the window it hands over. 12 tiles on a
# 2-column sheet is the whole of a 30s program at ~2.5s apart — enough that
# nothing between them is a surprise, few enough to stay one picture.
MCP_WATCH_FRAMES = int(os.getenv("MCP_WATCH_FRAMES", "12"))

# ...and what may be PULLED ONTO THIS BOX to make that shrunk copy. Only ever
# reached by a project with no proxy yet (an index still running or failed),
# where the sole copy of the footage is the user's 4K original: downloading it
# to re-encode would fill the dispatcher's scratch disk to answer a question
# the untouched link already answers.
MCP_VIDEO_DOWNLOAD_MAX_MB = float(os.getenv("MCP_VIDEO_DOWNLOAD_MAX_MB",
                                            "2048"))

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
# real encode AND stay UNDER the executor's own request timeout, so that when a
# job wedges the executor kills it shortly after the dispatcher has given up —
# rather than leaving an orphan burning 8 vCPU alongside the retry.
#
# 1500 (was 3300), paired with `--timeout 1800` on the Cloud Run service.
# Two wedged finals once ran the old 3600s cap to the second, on a box billed
# per instance-second, while the longest HEALTHY job on this hardware is ~300s.
# 1500 is 5x that: generous for a real 4K final, and a wedge now costs 25
# minutes instead of an hour. On timeout the dispatcher requeues, which is safe
# because renders are idempotent and cached by fingerprint.
#
# CHANGE THE TWO TOGETHER. This is deliberately a code default rather than a
# Render env var: per-service envs for a paired constant are exactly how
# PIPELINE_VERSION drifted in July and put every project into a re-index loop.
# If you raise Cloud Run's --timeout, raise this to match; it must always be
# the smaller of the two.
REMOTE_EXECUTOR_TIMEOUT_S = int(os.getenv("REMOTE_EXECUTOR_TIMEOUT_S", "1500"))

# ONE TIMEOUT FOR EVERY JOB KIND WAS THE THING THAT CAPPED VIDEO LENGTH
# (round 57).
#
# A 20-second reel's preview and a 1-hour final are not the same wait, and
# sizing a single number for both means sizing it for the short one: 1500s is
# five minutes more than the longest healthy job we had ever SEEN, which is a
# different thing from the longest job that should be ALLOWED. Measured on the
# executor, encodes run 0.5-1.8x realtime (filters, not pixels: a 270x480
# source still took 0.93x), so an hour-long programme needs a budget in the
# thousands of seconds or it cannot finish at all — and the failure arrives
# after the user has already waited.
#
# The pairing invariant is unchanged and still load-bearing: the dispatcher
# must give up BEFORE the executor, or a wedged job is requeued while the
# original keeps burning 8 vCPU beside the retry. So every value here must stay
# under EXECUTOR_REQUEST_TIMEOUT_S, which mirrors Cloud Run's `--timeout`.
# worker/tests/test_executor_timeouts.py asserts both the ordering and that
# DEPLOY_EXECUTOR.md still documents the same number.
#
# Raising the cap back toward Cloud Run's 3600s maximum is safer than it was in
# round 48, when two wedged finals ran the old cap to the second. What changed
# is that the encodes that could wedge now run under media.run()'s stall
# watchdog (it fires on ffmpeg producing NO progress, long before any of these
# ceilings), so these are a backstop for a pathological job rather than the
# primary defence they used to be.
EXECUTOR_REQUEST_TIMEOUT_S = int(
    os.getenv("EXECUTOR_REQUEST_TIMEOUT_S", "3600"))
# Music/voice stem separation (round 97, #7 — see worker/stems.py).
# htdemucs is the quality default; the executor's 8 vCPU run it near
# realtime, and the per-source cache means each video pays separation once,
# ever. The source cap keeps one separation inside a turn's patience — a
# longer video gets an honest "up to N minutes" refusal instead of a wall.
STEMS_MODEL = os.getenv("STEMS_MODEL", "htdemucs").strip()
STEMS_MAX_SOURCE_S = float(os.getenv("STEMS_MAX_SOURCE_S", "720"))
STEMS_TIMEOUT_S = float(os.getenv("STEMS_TIMEOUT_S", "1380"))

REMOTE_EXECUTOR_TIMEOUTS = {
    # A preview is deliberately the impatient one: it renders from the 540p
    # proxy and a user is watching a spinner while it runs.
    "preview": int(os.getenv("REMOTE_TIMEOUT_PREVIEW_S", "1500")),
    # Stem separation (round 97): demucs on CPU runs near realtime, the
    # source is capped at STEMS_MAX_SOURCE_S, and the result is cached per
    # source forever — so this bounds ONE first-ever separation per video.
    "stems": int(os.getenv("REMOTE_TIMEOUT_STEMS_S", "1500")),
    # A final renders the customer's ORIGINAL at source resolution — the one
    # job worth waiting an hour for, because the alternative is telling someone
    # their finished edit cannot be exported.
    "final": int(os.getenv("REMOTE_TIMEOUT_FINAL_S", "3400")),
    # An index is once per video and everything else waits on it.
    "index": int(os.getenv("REMOTE_TIMEOUT_INDEX_S", "3400")),
    # A web capture is bounded on the FAR side by WEB_RECORD_WALL_S (90s),
    # which covers page load, the scripted steps and the screencast. This is
    # that plus room for a Cloud Run cold start and the upload of the finished
    # mp4 — and it is the one kind a user is synchronously waiting on inside an
    # agent turn, so it must stay far below AGENT_TURN_TIMEOUT_S rather than
    # near the executor's own ceiling.
    "capture": int(os.getenv("REMOTE_TIMEOUT_CAPTURE_S", "180")),
    # Frame extraction from a stored original (round 62): dominated by the
    # executor pulling the source object from storage — a few hundred MB on
    # Cloud Run's own pipe — plus a handful of single-frame seeks. Like
    # capture, a user is synchronously inside an agent turn waiting on it, so
    # it sits far below AGENT_TURN_TIMEOUT_S (720), never near the executor's
    # ceiling.
    "frames": int(os.getenv("REMOTE_TIMEOUT_FRAMES_S", "240")),
    # Quad tracking through a takeover window (round 63): the same staging
    # cost as frames plus one linear decode of a <=5s window at 960 wide and
    # a per-frame LK pass — seconds of compute, minutes of download on a big
    # original. Synchronously inside an agent turn, same ceiling logic.
    "track": int(os.getenv("REMOTE_TIMEOUT_TRACK_S", "240")),
    # The text-behind matte (round 64): stages the 540p PROXY (small), decodes
    # one <=15s window and runs the person model on a bounded number of frames
    # (matte.SEG_BUDGET). Synchronously inside an agent turn — same ceiling
    # logic as capture/frames/track, sized for the worst budgeted window plus
    # a cold start.
    "matte": int(os.getenv("REMOTE_TIMEOUT_MATTE_S", "300")),
    # The takeover's guided content-lock (round 65d): stages the host clip's
    # original plus the handoff recording, extracts a handful of frames and
    # runs one SIFT match. Synchronously inside an agent turn — same ceiling
    # logic as frames/track.
    "smatch": int(os.getenv("REMOTE_TIMEOUT_SMATCH_S", "240")),
    # The erase/repaint pass (round 67): decodes + re-encodes every frame of
    # the ORIGINAL (bounded by CLEAN_MAX_SOURCE_S / CLEAN_MAX_MPX_SECONDS),
    # optionally chained with the cursor pass. Synchronously inside an agent
    # turn, so it must stay under AGENT_TURN_TIMEOUT_S (720) with margin for
    # the turn's other steps.
    "clean": int(os.getenv("REMOTE_TIMEOUT_CLEAN_S", "600")),
}


def executor_timeout_for(job_type):
    """Dispatcher-side HTTP timeout for one job kind."""
    return REMOTE_EXECUTOR_TIMEOUTS.get(job_type, REMOTE_EXECUTOR_TIMEOUT_S)
# Port the executor's HTTP server binds (Cloud Run injects $PORT, default 8080).
EXECUTOR_PORT = int(os.getenv("PORT", "8080"))
# How many index artifacts (proxy, wav, thumbnails, contact sheets) are PUT to
# object storage at once. They are independent objects and every upload is
# blocked in a socket with the GIL released, so this is pure network
# concurrency — it costs no CPU on a box whose CPU is the actual bottleneck.
# Serial uploads were ~24.5s of an index (about 40% of it) and that time is
# straight off every user's wait before "your video is ready".
# Lower it if the storage provider starts rate-limiting; 1 restores the old
# sequential behaviour exactly.
UPLOAD_PARALLELISM = int(os.getenv("UPLOAD_PARALLELISM", "8"))
# Shot thumbnails extracted at once. One per shot and there can be hundreds:
# pulled serially, a real 141-shot upload spent 48s seeking before it had even
# started uploading them. Each is an independent ffmpeg seek that is mostly
# I/O and single-threaded decode, so they overlap well.
THUMB_PARALLELISM = int(os.getenv("THUMB_PARALLELISM", "4"))
STALE_AFTER_S = 120           # running + no heartbeat for this long => reclaimable
MAX_ATTEMPTS_MEDIA = 3        # first run + 2 retries
MAX_ATTEMPTS_AGENT = 1        # agent turns are not auto-retried (user can resend)
# THE BACKSTOP UNDER THE REFUNDABLE BUDGET (round 73).
#
# `attempts` is a FAIRNESS counter: release_jobs gives one back on every
# dispatcher SIGTERM, because a deploy killing a job is our fault, not the
# job's. That is right, and it stays. But it means MAX_ATTEMPTS_MEDIA bounds
# nothing on its own — a job that keeps getting caught by deploys is handed
# another life every time, and each life on the executor is a full
# FFMPEG_TIMEOUT_S of 8 vCPU.
#
# On Jul 26 2026 final job=836 ran FIVE times against a cap of three, each
# attempt burning the whole 3000s wall-clock, and 50 deploys in that week is
# what paid for it. Four jobs like it were 9.37 hours — half a $14 week.
#
# So the money question and the fairness question get separate counters:
# total_claims counts what we have PHYSICALLY SPENT running this job and is
# never given back. Sized to absorb a few genuine deploy interruptions on top
# of the three real tries, then stop for good.
MAX_CLAIMS_ABSOLUTE = int(os.getenv("MAX_CLAIMS_ABSOLUTE", "6"))

# How long a tray with NO main footage behind it must sit completely untouched
# before the reaper commits it for the user (db.rescue_abandoned_trays).
# 300s: long enough that a person picking files out of a phone gallery, or a
# slow second upload, is never committed out from under — the scan requires no
# asset activity at all in the window — and short enough that someone still
# looking at the page gets their video analyzing while they are there rather
# than never. Only ever fires on a project that has no working state to lose.
TRAY_RESCUE_AFTER_S = int(os.getenv("TRAY_RESCUE_AFTER_S", "300"))

AGENT_MAX_ITERATIONS = 30
# A model-call ceiling is only an emergency runaway backstop. It must not be a
# normal editing limit: project 501 reached the old 16-call wall while still
# making valid documentary edits, auto-rendered a partial rebuild, and told the
# user to ask again. Progress, the user's spend budget, the inactivity window,
# cycle detection and the 3 x 30 iteration walls already bound real work. Keep
# this at least as large as all three productive passes so a stale Render env
# cannot silently restore the customer-visible 16-call cutoff; 180 is still a
# hard poison-pill ceiling if every other guard fails.
AGENT_MAX_MODEL_CALLS = min(
    180, max(90, int(os.getenv("AGENT_MAX_MODEL_CALLS", "90"))))
# How many times a turn that hits the iteration ceiling MID-WORK may resume
# itself (round 96b, project 383: 30 productive calls in 7.5 min, then a
# canned English "tell me to continue" at a Portuguese-speaking user with
# half the 900s time budget unspent). Each continuation is a fresh pass —
# rebuilt state, small context — over the SAME user message, sharing the
# turn's wall clock and spend cap, and it only happens when the ending pass
# actually landed edits or renders; a pass that moved nothing is a runaway
# and still stops at the wall. 2 -> at most 90 iterations, inside the same
# one shared model-call ceiling / credit bounds that always apply.
AGENT_AUTO_CONTINUES = int(os.getenv("AGENT_AUTO_CONTINUES", "2"))
AGENT_TEMPERATURE = 0.2
# Completion ceiling for ONE agent step. 8000 (was a hardcoded 2000).
#
# A REASONING model spends this budget before it ever reaches `content`: on
# Jul 26 2026 three turns in a row came back with completion_tokens exactly
# 2000, empty content and no tool calls — the model thought for 30-40s, hit
# the ceiling and said nothing, and the loop posted "I only reviewed the video
# — the edit was not changed" at a paying user who had just said "Well change
# it". He asked "Why not" and got the same line again. 0 of 703 grok-4.5 calls
# ever hit the cap; 3 of 113 deepseek-v4-pro calls did, and all 3 killed the
# turn. You pay only for tokens actually generated, so a high ceiling costs
# nothing on a normal step — the turn budget and AGENT_MAX_ITERATIONS are what
# bound spend. AGENT_MAX_TOKENS_CEILING is the retry's second, larger try.
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "8000"))
# 32000 (was 16000): reasoning tokens spend from the same budget, and at
# effort=max a planning step can out-think 16k before reaching content. The
# doubling retry ladder is 8k -> 16k -> 32k (two retries); tokens are billed
# only when generated, so the ceiling costs nothing on a normal step.
AGENT_MAX_TOKENS_CEILING = int(os.getenv("AGENT_MAX_TOKENS_CEILING", "32000"))
# The honesty layer's redraft. Same trap: a reasoning model that burns the
# budget thinking returns an empty redraft, which is then discarded in favour
# of the canned fallback — the user reads a non-answer twice over.
AGENT_REPLY_MAX_TOKENS = int(os.getenv("AGENT_REPLY_MAX_TOKENS", "4000"))
# Stall window, not a total turn limit. When this much time passes with no EDL
# write, successful preview or created media asset, the loop stops honestly.
# Productive work refreshes the window as often as necessary, so a complex
# edit is never cut off merely because its total wall time crossed ten minutes.
# The hard max keeps a stale 900s production env from waiting fifteen minutes
# before detecting an actually stuck turn.
AGENT_TURN_TIMEOUT_S = min(
    600.0, float(os.getenv("AGENT_TURN_TIMEOUT_S", "600")))
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
# Model prices ($/1M tokens) — the FALLBACK, for a model that model_prices.py
# does not list. Every known model is priced from its own row's `model` column
# (see model_prices.MODEL_PRICES) precisely because one turn can now run on a
# different model from another: a global constant that is right for DeepSeek is
# silently wrong for Grok, and a silent constant-factor billing error is the bug
# class this whole area keeps producing.
#
# Default = OpenAI GPT-5.6 Luna ($0.20 in / $1.20 out, list price from
# OpenAI's own model page Jul 31 2026) — ~9x cheaper in and ~3x cheaper out
# than the DeepSeek V4 Pro it replaced.
LLM_PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "0.20"))
LLM_PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "1.20"))
# Cached input ($/1M). A provider that serves a repeated prompt PREFIX from
# cache charges a fraction of a miss for it — DeepSeek $0.003625/1M (480x
# cheaper), Grok $0.30/1M (6.7x cheaper). An agent turn re-sends the same system
# prompt + ~60 tool schemas every iteration, so most input after the first step
# is a cache HIT, and billing hits at the miss rate over-charges a multi-step
# turn several-fold.
#
# DO NOT set this equal to LLM_PRICE_IN_PER_M "because provider X has no
# caching" — that is what the previous comment here said about Grok, and it was
# wrong: Grok's invoice bills cached input as its own line at $0.30/1M, and
# 86.6% of input hits it. Setting 2.0 would have re-created the exact
# over-charge that had just been fixed. Per-model rates live in
# model_prices.MODEL_PRICES; this constant only covers unlisted models, and for
# one that genuinely has no caching the reported hit count is 0, which makes the
# rate inert anyway.
# Default tracks Luna's cached-input list price ($0.02/1M — 10x cheaper
# than a miss).
LLM_PRICE_CACHED_IN_PER_M = float(
    os.getenv("LLM_PRICE_CACHED_IN_PER_M", "0.02"))

# What price_for() falls back to for a model that is not in MODEL_PRICES.
PRICE_FALLBACK = {"in": LLM_PRICE_IN_PER_M,
                  "cached_in": LLM_PRICE_CACHED_IN_PER_M,
                  "out": LLM_PRICE_OUT_PER_M,
                  "reasoning_separate": False}
# Per-image charge (1 credit = $0.01). 0.055 tracks grok-imagine-image-quality
# (see IMAGE_GEN_MODEL); if you switch IMAGE_GEN_MODEL, set this to that tier's
# real per-image price or credits drift from cost.
IMAGE_PRICE_USD = float(os.getenv("IMAGE_PRICE_USD", "0.055"))
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
# ...and the bound that DURATION alone does not express: frame area. The clean
# pass runs on the dispatcher — the smallest box in the fleet — and a 4K frame
# costs 8x a 1080p one in decode buffer, numpy working set and x264 state. Two
# real agent turns were killed outright on 2026-07-25 (a 1282x1596 erase and a
# 1440x2560 one): the container was OOM-killed, so the job did not fail, the
# WORKER died, and every other user's in-flight turn went down with it. The
# encoder is now bounded (see inpaint.clean_video) and the total work is capped
# here, so an oversized repaint is refused honestly instead of taking the
# service down. Megapixel-seconds = width*height/1e6 * duration.
CLEAN_MAX_MPX_SECONDS = float(os.getenv("CLEAN_MAX_MPX_SECONDS", "900"))
# x264 keeps `rc-lookahead` decoded frames alive at all times. At 4K that alone
# is ~1 GB per encoder before anything else. The clean pass does not need
# lookahead quality — it is re-encoding footage it just decoded.
CLEAN_X264_THREADS = int(os.getenv("CLEAN_X264_THREADS", "2"))
CLEAN_X264_LOOKAHEAD = int(os.getenv("CLEAN_X264_LOOKAHEAD", "10"))
# Frames sampled when LOOKING for burned-in text. Detection reads the proxy and
# input-seeks, so each sample is a seek, not a decode; 28 covers a caption that
# is only on screen for part of the video without making the scan noticeable.
CLEAN_DETECT_SAMPLES = int(os.getenv("CLEAN_DETECT_SAMPLES", "28"))

# ── Custom filter chains (round 96) ─────────────────────────────────────────
# The dry run encodes this many seconds of the real proxy twice — once plain
# as the control, once through the agent's chain — so a chain is validated
# against the actual footage and its COST is a measured ratio, not a guess.
CUSTOM_FILTER_PROBE_S = float(os.getenv("CUSTOM_FILTER_PROBE_S", "0.6"))
# Reject a chain whose probe costs more than this many times the control
# encode. The whole preset stylize stack runs ~3-5x; 8x admits every look a
# reasonable chain produces while refusing the minterpolate-class graphs
# that would turn a 30s preview into minutes.
CUSTOM_FILTER_MAX_COST = float(os.getenv("CUSTOM_FILTER_MAX_COST", "8.0"))
# Hard wall-clock cap on each probe run — a chain that stalls its own 0.6s
# probe would stall a render for minutes; kill it and reject with the tail
# of ffmpeg's own stderr.
CUSTOM_FILTER_PROBE_TIMEOUT_S = float(
    os.getenv("CUSTOM_FILTER_PROBE_TIMEOUT_S", "45"))

# ── The inpaint runs at the scale of the HOLE, not of the frame (round 88) ──
#
# cv2.inpaint(TELEA) reconstructs a hole by diffusing inward from its boundary.
# Its cost grows with the hole's AREA; the detail it can invent does not — past
# a few pixels from the edge it is producing smooth diffusion whatever the
# resolution. Running it at full frame scale on a big hole therefore buys
# nothing and costs everything: project 360 (Aug 5 2026) asked for a 0.9x0.45
# 'box' repaint of a 1080x1920 video — ~840,000 masked pixels, TELEA'd 330
# times, one per frame — and that single call took 425 seconds and ate the
# turn. Two earlier passes on the same footage took 120s and 162s.
#
# So the mask's area picks the scale: under the budget nothing changes (thin
# text strokes, where the boundary IS the output, keep full resolution), and a
# large fill is diffused at the resolution its own smoothness justifies and
# scaled back. Only masked pixels are ever taken from the scaled result, so
# the surrounding picture stays bit-exact either way.
INPAINT_MAX_PX = int(os.getenv("INPAINT_MAX_PX", "120000"))
INPAINT_MIN_SCALE = float(os.getenv("INPAINT_MIN_SCALE", "0.25"))

# Person-segmentation model for the text-behind matte (round 64). The ONNX
# file is baked into the image by the Dockerfile (same policy as the whisper
# model: never a runtime download). Missing file or missing onnxruntime =
# personseg.available() False = the matte builds the photometric way — the
# honest-off contract, nothing errors. On the dispatcher the model is present
# (one image, two roles) but deliberately NOT run: a forward pass per frame on
# the box that also runs agent turns is the round-60 objection, still valid —
# the matte job goes to the executor whenever one is configured.
MATTE_SEG_MODEL = os.getenv(
    "MATTE_SEG_MODEL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "models", "u2net_human_seg.onnx"))
# RobustVideoMatting (round 69) — the PRIMARY matte model. u2net above is a
# per-frame salient-object net, and matte v6-v9 spent four rounds gating its
# frame-to-frame flicker; RVM carries recurrent state across frames, so the
# mask is temporally stable because the MODEL is, not because post-processing
# fought it. Same honest-off ladder: file missing -> u2net rung -> photometric.
MATTE_RVM_MODEL = os.getenv(
    "MATTE_RVM_MODEL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "models", "rvm_mobilenetv3_fp32.onnx"))
# RVM runs on EVERY mask frame (that is what kills the round-68 lerp ghost —
# the mask that dimmed letters the walker had not reached yet). The budget
# bounds wall time on the executor by halving the mask RATE, never by strided
# sampling: measured 128 ms/frame at 960x540, so 500 frames is ~2 minutes
# against REMOTE_TIMEOUT_MATTE_S=300 with staging either side. A 2-8s beat
# masks at the full render rate; the 15s ceiling masks at 30 fps.
MATTE_RVM_BUDGET = int(os.getenv("MATTE_RVM_BUDGET", "500"))

PREVIEW_PRESET = os.getenv("PREVIEW_PRESET", "ultrafast")

# ── The preview is a PROOF, not a deliverable (round 88) ────────────────
#
# A preview was rendered at the source's full frame and full rate: a phone
# reel is 1080x1920 @ 60fps, so every "let me check that" re-encoded 124M
# pixels a second through the whole filtergraph. Measured over 379 real
# previews: 27.3s mean, of which ENCODE is 19.2s — and it scales with the
# footage, not the edit (a 186s video: 144.6s; a 140s video: 205.5s). It is
# the single largest consumer of wall time in the product — 435 calls,
# 15,793 seconds, more than every other tool combined — and it is what
# actually spends an agent's turn budget: the turn that timed out on
# project 360 had made its edits in 95 seconds of model time.
#
# Nothing that reads a preview needs those pixels:
#   - the studio plays it in a panel a few hundred pixels wide;
#   - the AGENT reads it as contact-sheet tiles that sheets.py builds at
#     480x270 — it has never once seen a preview pixel at full size;
#   - look_at/look_at_asset sample the PROXY and the source assets, not the
#     render, so aiming and verification are untouched.
# The export is unchanged: FINAL renders stay at full frame and full rate.
#
# Capping the long edge at 1280 takes a 1080x1920 reel to 720x1280 (44% of
# the pixels) and 60fps to 30 (half the frames) — ~4.4x less throughput for
# a picture the reader cannot tell apart. W/H flow into the whole graph
# (ASS PlayRes, watermark geometry, text sizes, zoom viewports), so every
# element scales with the frame and the preview stays a true proof of the
# edit. Set PREVIEW_MAX_LONG_EDGE=0 to render previews at source size.
PREVIEW_MAX_LONG_EDGE = int(os.getenv("PREVIEW_MAX_LONG_EDGE", "1280"))

# Round 98: speculative previews. After a step that landed writes the loop
# starts the encode immediately so it overlaps the next model call; the
# agent's own render_preview then ADOPTS the running job (see
# db.pending_preview_job) instead of enqueueing a second encode. MAX bounds
# wasted encodes on a turn that keeps writing version after version —
# stitching keeps each one cheap, but not free.
# Intermediate speculative encodes produced 52 previews across five projects,
# most obsolete before completion. Batch first; prove the candidate once and
# allow one repair proof.
SPECULATIVE_PREVIEWS = os.getenv("SPECULATIVE_PREVIEWS", "0") == "1"
SPECULATIVE_PREVIEWS_MAX = min(
    1, max(0, int(os.getenv("SPECULATIVE_PREVIEWS_MAX", "1"))))
AGENT_MAX_PREVIEWS_PER_TURN = min(
    2, max(1, int(os.getenv("AGENT_MAX_PREVIEWS_PER_TURN", "2"))))
# The height a preview is actually WRITTEN at. This number is not new — the
# graph has always ended with `scale=-2:min(480,...)` — but it lived as a
# literal at the end of the filter chain, which meant every filter before it
# ran at the SOURCE proxy's height and had its work thrown away. Declared here
# and applied in renderer.preview_geometry so one cap decides the size, and
# the filters run at the size that ships. Set to 0 to render previews at the
# long-edge cap alone. See preview_geometry for the measurement (25%).
PREVIEW_MAX_HEIGHT = int(os.getenv("PREVIEW_MAX_HEIGHT", "480"))
PREVIEW_MAX_FPS = float(os.getenv("PREVIEW_MAX_FPS", "30"))

# Final exports: veryfast/CRF20 is effectively transparent for talking-head /
# screen content and several times faster than the old medium/CRF18.
FINAL_PRESET = os.getenv("FINAL_PRESET", "veryfast")
FINAL_CRF = int(os.getenv("FINAL_CRF", "20"))

SILENCE_NOISE_DB = "-35dB"
SILENCE_MIN_S = 0.6
SCENE_THRESHOLD = float(os.getenv("SCENE_THRESHOLD", "27.0"))

# Vision-call cap during indexing: one contact sheet = 25 tiles = one vision
# call. A 3-hour shot-heavy video would otherwise fire proportionally many
# calls; beyond this many sheets we sample evenly across the video and record a
# warning so the cost is bounded and the degradation is visible.
MAX_VISION_SHEETS = int(os.getenv("MAX_VISION_SHEETS", "12"))

# ── Filmstrip tiles (round 84): the visual index the agent READS ITSELF ──
# Per-turn attachment budgets, in TILES (one tile = 4 labeled frames).
# The main footage's strip is the biggest sense; each uploaded clip gets a
# smaller strip; the turn total bounds the worst case (many clips). The
# strips themselves are built at index time (worker/tiles.py) and cached
# locally per sha, so these caps price CONTEXT, not compute.
TILES_MAIN_MAX = int(os.getenv("TILES_MAIN_MAX", "36"))
TILES_CLIP_MAX = int(os.getenv("TILES_CLIP_MAX", "10"))
TILES_TURN_MAX = int(os.getenv("TILES_TURN_MAX", "60"))

# Still images (uploads and generated cards) ride in the same senses block,
# one downscaled frame each (round 95). Before this they were text-only
# inventory lines: the agent either spent a tool call per photo to learn
# what it showed (project 380: 8 photos, 8 look_at_asset calls before the
# first edit) or placed it blind — and a CANVAS program built purely from
# stills started with no eyes at all. Each attached image prices like
# roughly one tile; the cap bounds a 40-photo dump. IMAGE_ATTACH_MAX_PX is
# the long side of the attached copy (a tile is 960px wide — same order).
IMAGES_TURN_MAX = int(os.getenv("IMAGES_TURN_MAX", "20"))
IMAGE_ATTACH_MAX_PX = int(os.getenv("IMAGE_ATTACH_MAX_PX", "960"))

# How often the index looks at the picture, in SOURCE seconds (round 69).
#
# The sampling unit used to be the SHOT, and 46% of real prod videos are a
# single shot — a locked-off talking head, a screen recording, a timelapse —
# so the agent's entire visual description of them came from ONE frame. The
# worst measured case was 19.3 minutes of footage described by a frame at
# 9:38. See worker/visual.py for why shots could not simply be subdivided.
#
# 4s is chosen against the median prod video (79s -> ~20 tiles -> ONE sheet,
# exactly today's cost) while a 10-minute video gets 150 tiles instead of
# however many shots it happened to have. The ceiling is unchanged:
# MAX_VISION_SHEETS x 25 tiles, which has bounded this since round 22 and
# which a single-shot video used to spend 1 of.
VISUAL_SAMPLE_S = float(os.getenv("VISUAL_SAMPLE_S", "4.0"))
# Contact sheets are captioned concurrently — they are independent network
# calls, and going from 1 sheet to as many as 12 would otherwise add minutes
# of pure round-trip wait to an index. Kept small: the point is to overlap
# latency, not to hammer the provider.
VISION_CAPTION_PARALLELISM = int(os.getenv("VISION_CAPTION_PARALLELISM", "4"))

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
OUTRO_VERSION = 6      # v6: pure 9:16, the WHITE robot untouched on nothing
                       # at all (no glow, no panel, no recolour), the
                       # sentence UNDER it, the wordmark as the only large
                       # type. See tools/build_endcard.py

# ── Shorts mode (round 99) ───────────────────────────────────────────────
# One shorts_plan job cuts a long video into at most this many child clips.
# ~1 clip per 5 minutes of source is the planner's target below the cap.
SHORTS_MAX_CLIPS = int(os.getenv("SHORTS_MAX_CLIPS", "8"))
# Flat credits per finished clip, charged on top of the run's model cost —
# a shorts run fans out N final renders that a plain chat turn never does.
SHORTS_CLIP_CREDITS = float(os.getenv("SHORTS_CLIP_CREDITS", "2.0"))

# ── Complex-script text rendering (round 44) ─────────────────────────────
# Stamped as `gfx_shape_v` on every render and compared ONLY for EDLs whose
# text actually contains a shaping-sensitive script (Arabic/Hebrew/Indic/
# Thai/...). Those renders are known-wrong before v1: letter-spacing forced
# libass onto its SIMPLE shaper, so the text burned in unjoined AND reversed.
# Scoping the bust to shaped EDLs keeps every Latin render cached — a blanket
# invalidation would re-encode the whole platform on a ~1 vCPU box.
# Bump when the shaped-text emission changes again.
GFX_SHAPING_VERSION = 1

# ── Where transitions land (round 48) ─────────────────────────────────────
# Stamped as `trans_v` on every render and compared ONLY for EDLs that carry a
# transition, so nothing else loses its cache. Renders below v1 put a junction
# effect on EVERY cut — which on a silence-cut talking head is a full-screen
# effect every couple of seconds through one continuous shot, because
# cut_silences leaves one junction per removed pause and they are all jump
# cuts in the same framing. A real customer's preview shipped with 45 whip
# pans through one shot. Those renders are known-wrong, and the cache is keyed
# on EDL VERSION rather than content, so without this stamp she would keep
# being served the broken file forever. Bump if junction selection changes
# again.
TRANSITION_VERSION = 2

# Round 79j — the SEQUENCE is as long as its content: unmuted music past the
# last scene extends the render over black instead of being cut off at the
# picture's end. Renders made before this stamp are exactly program-length,
# so an EDL whose music runs longer must not be served from that cache.
# Bump if the extension semantics change.
MUSIC_TAIL_VERSION = 1

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
# 2 = wordmark capitalised ("EDITED BY VALMERA AGENT"). Without this bump the
# already-rendered exports stay stamped wm_v=1, pass the freshness gate, and
# serve the old lowercase mark forever — which is precisely the end-card bug.
WATERMARK_VERSION = 2
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
# Capitalised at the owner's request. Set as a literal rather than .upper()'d
# at render time so an operator overriding WATERMARK_TEXT gets exactly the
# casing they typed instead of having it silently rewritten.
WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "EDITED BY VALMERA AGENT")
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

# MUST stay below the platform's own request timeout, or it is dead code: at
# 5400 it sat ABOVE Cloud Run's 3600s, so Cloud Run always killed the request
# first and this cap never once fired. That cost an hour of a pinned slot with
# no honest error anywhere — the job just stopped existing. Keep it under
# `gcloud run services describe valmera-executor --format='value(spec.template.spec.timeoutSeconds)'`.
FFMPEG_TIMEOUT_S = int(os.getenv("FFMPEG_TIMEOUT_S", "3000"))
# A stalled encode stops emitting -progress lines but keeps its stdout pipe
# open, so the progress reader would block forever (this once froze the only
# media slot for hours). Kill an encode that goes this long with no progress,
# well before the full wall-clock cap above.
FFMPEG_STALL_TIMEOUT_S = int(os.getenv("FFMPEG_STALL_TIMEOUT_S", "300"))
# An encode that RUNS AWAY is loud, not silent: it emits -progress forever, so
# the stall cap above can never fire and the wall-clock cap is an hour away.
# But we know exactly how long the output must be, so out_time sailing past it
# is not a heuristic — it is proof the filtergraph cannot terminate. Kill on
# that and a runaway dies in seconds instead of occupying a slot for an hour.
# Generous multiplier + floor so no legitimate encode is ever near it.
FFMPEG_OVERRUN_FACTOR = float(os.getenv("FFMPEG_OVERRUN_FACTOR", "1.25"))
FFMPEG_OVERRUN_FLOOR_S = float(os.getenv("FFMPEG_OVERRUN_FLOOR_S", "10"))


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
