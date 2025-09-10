#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "${here}/../.." && pwd)"

# Default port matches code default unless overridden
PORT="${PORT:-5055}"

usage() {
  cat <<USAGE
Run Falkland V2 webdash.

Usage:
  PORT=5060 $0
  $0 -p 5060

Environment:
  PORT   Port to bind (default: 5055)
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage; exit 0
fi

if [[ "${1:-}" == "-p" || "${1:-}" == "--port" ]]; then
  PORT="${2:-$PORT}"; shift 2 || true
elif [[ "${1:-}" =~ ^[0-9]+$ ]]; then
  PORT="$1"; shift || true
fi

py="${root}/.venv/bin/python3"
if [[ ! -x "$py" ]]; then
  py="$(command -v python3)"
fi

export PORT
export PYTHONUNBUFFERED=1
echo "[webdash] starting at http://127.0.0.1:${PORT} using ${py}"
exec "$py" "${here}/webdash.py"

