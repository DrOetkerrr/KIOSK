#!/usr/bin/env bash
# Strict mode: fail fast and propagate errors
set -Eeuo pipefail
IFS=$'\n\t'

# cd to repo root (directory this script lives in)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "[ERROR] .venv not found at $SCRIPT_DIR/.venv. Please create and install dependencies." >&2
  echo "        Try: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# Ensure local imports resolve
export PYTHONPATH=.

PORT=${PORT:-5055}

# Source .env if present to load secrets (e.g., OPENAI_API_KEY)
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

# Always run via module to ensure a single module instance across imports
echo "Starting Falkland V2 dashboard on http://127.0.0.1:${PORT} …"
export PORT
exec python -u -m projects.falklandV2.serve "$@"
