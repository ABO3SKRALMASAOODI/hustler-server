# Valmera media+agent worker.
# Render: create a Background Worker, Runtime = Docker,
# Dockerfile path = worker/Dockerfile, Docker context = worker/
FROM python:3.11-slim

# fontconfig + Noto give libass real glyphs to fall back to when the caption
# font (DejaVu Sans) lacks a script. Without Devanagari/Arabic/CJK coverage,
# non-Latin captions render as tofu boxes AND spam per-frame "glyph not found"
# warnings — which used to flood ffmpeg's stderr and deadlock the encoder.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg fontconfig fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium for website capture (worker/webrecord.py). --with-deps pulls the
# shared libraries Chromium needs on slim Debian; the browser lands in
# PLAYWRIGHT_BROWSERS_PATH so it is part of the image, never a runtime
# download. If this layer is dropped, webrecord.available() turns the whole
# capability off honestly — nothing else breaks.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Bake the whisper model into the image so it is NOT re-downloaded (~0.5GB)
# on every container start — that download added big latency to the first job
# after each deploy and compounded media-slot contention. HF_HOME points at an
# in-image path so faster-whisper reads the baked cache at runtime.
ENV HF_HOME=/opt/hf
# Keep in sync with worker/config.py WHISPER_MODEL. 'medium' is the accuracy
# default; override with --build-arg WHISPER_MODEL=small for a lighter image.
ARG WHISPER_MODEL=medium
RUN python -c "from faster_whisper import download_model; \
    download_model('${WHISPER_MODEL}')"

COPY . .

ENV PYTHONUNBUFFERED=1 \
    WORKER_TMP_DIR=/tmp/valmera

CMD ["python", "main.py"]
