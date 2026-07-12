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

COPY . .

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/tmp/hf \
    WORKER_TMP_DIR=/tmp/valmera

CMD ["python", "main.py"]
