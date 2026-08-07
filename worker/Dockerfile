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
       xz-utils \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg 8.1 static (round 91): bookworm's apt ffmpeg is 5.1, the version the
# renderer carries explicit workarounds for (the seg_dur trim in
# _normalize_video) and NOT the version anything is developed or tested
# against — every dev machine and itest runs 8.1.x. The BtbN static GPL build
# (includes libass + x264, reads system fontconfig) aligns prod with the
# tested line. DEFAULT ON since the Aug 6 2026 canary (revision 00090):
# same 59s graded program encoded in 20.6s vs 29s on 5.1 (four warm samples
# within 0.07s), and the caption sheet — dynamic emphasized words, title
# cards, styled text — rendered pixel-correct through system fontconfig.
# FFMPEG_STATIC=0 falls back to the apt build if a future ffmpeg surprise
# needs unwinding.
ARG FFMPEG_STATIC=1
RUN if [ "$FFMPEG_STATIC" = "1" ]; then \
      python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz', '/tmp/ff.tar.xz')" \
      && tar -xJf /tmp/ff.tar.xz -C /tmp \
      && install -m755 /tmp/ffmpeg-*/bin/ffmpeg /usr/local/bin/ffmpeg \
      && install -m755 /tmp/ffmpeg-*/bin/ffprobe /usr/local/bin/ffprobe \
      && rm -rf /tmp/ff.tar.xz /tmp/ffmpeg-* \
      && ffmpeg -version | head -1; \
    fi

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

# Music/voice stem separation (round 97 — worker/stems.py). Torch is CPU-only
# (the +cpu wheel index keeps ~1.5GB of CUDA out of the image) and the
# htdemucs weights are baked so a scale-to-zero cold start never downloads a
# model. Keep the model name in sync with worker/config.py STEMS_MODEL.
# stems.available() gates on the import, so dropping this layer turns the
# capability off honestly (/health stops advertising "stems") — nothing else
# breaks. STEMS=0 skips the layer for a slim build.
# torch/torchaudio are PINNED to the pairing demucs 4.0.1 actually calls:
# unpinned, the resolver took torchaudio's TorchCodec era, whose save()
# raises "TorchCodec is required" — the first live separation ran the model
# for 230s and died WRITING the wav (job at 23:15 Aug 7 2026). 2.4.1 is the
# last classic-backend line and the standard demucs companion.
ARG STEMS=1
ENV TORCH_HOME=/opt/torch
RUN if [ "$STEMS" = "1" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.4.1 torchaudio==2.4.1 \
      && pip install --no-cache-dir demucs==4.0.1 \
      && python -c "from demucs.pretrained import get_model; get_model('htdemucs'); print('htdemucs weights baked')"; \
    fi

COPY . .

ENV PYTHONUNBUFFERED=1 \
    WORKER_TMP_DIR=/tmp/valmera

CMD ["python", "main.py"]
