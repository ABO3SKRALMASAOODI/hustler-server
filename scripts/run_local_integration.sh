#!/usr/bin/env bash
# No-docker local harness for scripts/integration_test.py (macOS/Linux).
# Boots: local Postgres (initdb into $WORKDIR/pg), moto S3 server, the fake
# LLM, and the worker — then runs the acceptance test against them.
#
#   WORKDIR=/tmp/valmera-itest PYTHON=python3 bash scripts/run_local_integration.sh
#
# Requires: postgres binaries (initdb/pg_ctl), ffmpeg+ffprobe on PATH, and a
# python with backend+worker requirements plus moto[server] installed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKDIR="${WORKDIR:-/tmp/valmera-itest}"
PYTHON="${PYTHON:-python3}"
PGPORT="${PGPORT:-54329}"
MOTO_PORT="${MOTO_PORT:-9911}"
LLM_PORT="${LLM_PORT:-8189}"

mkdir -p "$WORKDIR"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  pg_ctl -D "$WORKDIR/pg" stop -m fast >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── Postgres ─────────────────────────────────────────────────────────
if [ ! -d "$WORKDIR/pg" ]; then
  initdb -D "$WORKDIR/pg" -U valmera --auth=trust >/dev/null
fi
pg_ctl -D "$WORKDIR/pg" -o "-p $PGPORT -k $WORKDIR" -l "$WORKDIR/pg.log" start >/dev/null
createdb -h 127.0.0.1 -p "$PGPORT" -U valmera valmera 2>/dev/null || true
export DATABASE_URL="postgresql://valmera@127.0.0.1:$PGPORT/valmera"

# ── moto S3 + fake LLM ───────────────────────────────────────────────
"$PYTHON" -m moto.server -p "$MOTO_PORT" >"$WORKDIR/moto.log" 2>&1 &
PIDS+=($!)
"$PYTHON" "$ROOT/scripts/fake_llm.py" "$LLM_PORT" >"$WORKDIR/llm.log" 2>&1 &
PIDS+=($!)
sleep 2

export S3_ENDPOINT="http://127.0.0.1:$MOTO_PORT"
export S3_ACCESS_KEY_ID=testing S3_SECRET_ACCESS_KEY=testing
export S3_BUCKET=valmera-test S3_REGION=us-east-1
export AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing
export OPENAI_BASE_URL="http://127.0.0.1:$LLM_PORT/v1"
export OPENAI_API_KEY=test AGENT_MODEL=fake VISION_MODEL=""
export WHISPER_MODEL="${WHISPER_MODEL:-tiny}" WHISPER_DEVICE=cpu
export WORKER_TMP_DIR="$WORKDIR/wtmp" SKIP_DB_INIT=0

# ── Worker ───────────────────────────────────────────────────────────
# exec so the captured PID is python itself, not a wrapper subshell —
# otherwise cleanup orphans the worker and a stale one steals jobs.
(cd "$ROOT/worker" && exec "$PYTHON" main.py >"$WORKDIR/worker.log" 2>&1) &
PIDS+=($!)
sleep 2

# ── Test ─────────────────────────────────────────────────────────────
"$PYTHON" "$ROOT/scripts/integration_test.py"
