# Valmera media+agent worker.
# Render: create a Background Worker, Runtime = Docker,
# Dockerfile path = worker/Dockerfile, Docker context = worker/
#
# Pinned to bookworm (Debian 12), NOT the floating `-slim` tag. `-slim` moved to
# trixie (Debian 13) in 2026, and Playwright 1.49's `install --with-deps` list
# still names `ttf-unifont` / `ttf-ubuntu-font-family` — packages trixie dropped
# (now `fonts-unifont`) — so the chromium layer died with "Package has no
# installation candidate" and every deploy failed. Bookworm is a supported
# Playwright target, so the dep list resolves cleanly. Bump this deliberately
# (and re-test the chromium layer) when moving to a newer Debian, never by
# floating the tag.
FROM python:3.11-slim-bookworm

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

# Person-segmentation model for the text-behind matte (round 64) — baked, same
# policy as the whisper model above: never a runtime download. If this layer is
# dropped, personseg.available() turns the model path off honestly and the
# matte falls back to the photometric build — nothing else breaks. Keep the
# path in sync with worker/config.py MATTE_SEG_MODEL.
# (python -c, not curl and not a heredoc — slim images do not ship curl, and
# RUN heredocs need BuildKit, which Cloud Build's classic builder lacks)
RUN mkdir -p /app/models && python -c "import urllib.request, os; \
    urllib.request.urlretrieve('https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx', '/app/models/u2net_human_seg.onnx'); \
    s = os.path.getsize('/app/models/u2net_human_seg.onnx'); \
    assert s > 100_000_000, f'model download truncated: {s} bytes'; \
    print('u2net_human_seg.onnx', s, 'bytes')"

# RobustVideoMatting (round 69) — the PRIMARY matte model; u2net above is now
# the fallback rung. RVM carries recurrent state across frames, which is what
# made the text-behind mask stop strobing (see worker/matte.py v10). Path in
# sync with worker/config.py MATTE_RVM_MODEL. Weights are GPL-3.0
# (PeterL1n/RobustVideoMatting) — run server-side only, never distributed.
RUN python -c "import urllib.request, os; \
    urllib.request.urlretrieve('https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx', '/app/models/rvm_mobilenetv3_fp32.onnx'); \
    s = os.path.getsize('/app/models/rvm_mobilenetv3_fp32.onnx'); \
    assert s > 10_000_000, f'model download truncated: {s} bytes'; \
    print('rvm_mobilenetv3_fp32.onnx', s, 'bytes')"

COPY . .

ENV PYTHONUNBUFFERED=1 \
    WORKER_TMP_DIR=/tmp/valmera

CMD ["python", "main.py"]
