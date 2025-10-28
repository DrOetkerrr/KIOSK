#!/usr/bin/env bash
# Launch Falkland V3 API and stations frontend together.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${FALKLANDV3_HOST:-127.0.0.1}"
PORT="${FALKLANDV3_PORT:-8000}"
FRONTEND_PORT="${FALKLANDV3_FRONTEND_PORT:-5173}"
SEED="${FALKLANDV3_RNG_SEED:-}"

if ! command -v uv >/dev/null 2>&1; then
  echo "[start_v3] 'uv' CLI not found. Install uv or adjust PATH." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[start_v3] 'npm' not found. Install Node.js 20+ to run the stations frontend." >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "[start_v3] Creating project virtualenv with uv sync…"
  uv --project "$ROOT_DIR" sync
fi

FRONTEND_DIR="$ROOT_DIR/frontend/stations"

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "[start_v3] Installing frontend dependencies (npm install)…"
  (cd "$FRONTEND_DIR" && npm install)
fi

cleanup() {
  trap - SIGINT SIGTERM EXIT
  if [[ -n "${frontend_pid:-}" ]]; then
    kill "$frontend_pid" 2>/dev/null || true
    wait "$frontend_pid" 2>/dev/null || true
  fi
  if [[ -n "${backend_pid:-}" ]]; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup SIGINT SIGTERM EXIT

backend_cmd=(uv --project "$ROOT_DIR" run python -m falklandv3.cli.serve_api --host "$HOST" --port "$PORT")
if [[ -n "$SEED" ]]; then
  backend_cmd+=(--seed "$SEED")
fi

echo "[start_v3] Starting API → http://${HOST}:${PORT}"
"${backend_cmd[@]}" &
backend_pid=$!

echo "[start_v3] Starting stations frontend → http://127.0.0.1:${FRONTEND_PORT}"
(cd "$FRONTEND_DIR" && npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT") &
frontend_pid=$!

echo "[start_v3] Press Ctrl+C to stop both services."
wait
