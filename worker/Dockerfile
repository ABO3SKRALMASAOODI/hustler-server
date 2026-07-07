# Valmera media+agent worker.
# Render: create a Background Worker, Runtime = Docker,
# Dockerfile path = worker/Dockerfile, Docker context = worker/
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/tmp/hf \
    WORKER_TMP_DIR=/tmp/valmera

CMD ["python", "main.py"]
