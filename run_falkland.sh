#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

exec "$(pwd)/.venv/bin/python" -u projects/FalklandV2/cli_main.py "$@"