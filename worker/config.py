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

# LLM — OpenAI-compatible only. Default: Alibaba DashScope compatible mode.
OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "qwen-plus")
# Empty string disables all vision features gracefully.
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-vl-plus")

# Transcription
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")   # cpu | cuda
WHISPER_COMPUTE = os.getenv(
    "WHISPER_COMPUTE", "int8" if WHISPER_DEVICE == "cpu" else "float16")

# Quotas / limits
MAX_UPLOAD_GB = float(os.getenv("MAX_UPLOAD_GB", "2"))
MAX_DURATION_S = float(os.getenv("MAX_DURATION_S", str(3 * 3600)))

# Worker tuning
TMP_DIR = os.getenv("WORKER_TMP_DIR", "/tmp/valmera")
POLL_INTERVAL_S = float(os.getenv("WORKER_POLL_INTERVAL_S", "2.0"))
MEDIA_SLOTS = int(os.getenv("WORKER_MEDIA_SLOTS", "1"))
AGENT_SLOTS = int(os.getenv("WORKER_AGENT_SLOTS", "2"))
HEARTBEAT_EVERY_S = 20
STALE_AFTER_S = 120           # running + no heartbeat for this long => reclaimable
MAX_ATTEMPTS_MEDIA = 3        # first run + 2 retries
MAX_ATTEMPTS_AGENT = 1        # agent turns are not auto-retried (user can resend)

AGENT_MAX_ITERATIONS = 30
AGENT_TEMPERATURE = 0.2
PREVIEW_WAIT_TIMEOUT_S = float(os.getenv("PREVIEW_WAIT_TIMEOUT_S", "900"))
TOOL_OUTPUT_CHAR_BUDGET = 12000   # ~3000 tokens

SILENCE_NOISE_DB = "-35dB"
SILENCE_MIN_S = 0.6
SCENE_THRESHOLD = float(os.getenv("SCENE_THRESHOLD", "27.0"))

FFMPEG_TIMEOUT_S = int(os.getenv("FFMPEG_TIMEOUT_S", "5400"))


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
