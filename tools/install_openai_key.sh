#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."  # repo root

ENV_FILE=".env"
KEY=""
AUTO_YES="false"

usage(){
  cat <<USAGE
Usage: tools/install_openai_key.sh [--key <sk-...>] [-y]

Stores OPENAI_API_KEY in .env (sourced automatically by run_falkland.sh).
Safe to re-run; it overwrites only the OPENAI_API_KEY line.

Options:
  --key VALUE   Provide the key non-interactively
  -y            Skip confirmation prompt
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key)
      shift; KEY="${1:-}" || true; shift || true;;
    -y|--yes)
      AUTO_YES="true"; shift;;
    -h|--help)
      usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ "$AUTO_YES" != "true" ]]; then
  echo "This will store your OpenAI API key in $ENV_FILE (not committed)."
  read -r -p "Proceed? [y/N] " ok
  case "${ok:-}" in
    y|Y) :;;
    *) echo "Aborted."; exit 1;;
  esac
fi

if [[ -z "${KEY}" ]]; then
  read -r -p "Enter OPENAI_API_KEY (starts with sk-): " KEY
fi
if [[ -z "${KEY}" ]]; then
  echo "No key entered. Aborting." >&2
  exit 1
fi

# Ensure .env exists
touch "$ENV_FILE"

# Remove existing key line(s), then append
grep -vE '^OPENAI_API_KEY=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
mv "$ENV_FILE.tmp" "$ENV_FILE"
echo "OPENAI_API_KEY=${KEY}" >> "$ENV_FILE"

# Optional defaults (don’t override if already set)
grep -qE '^OPENAI_TTS_MODEL=' "$ENV_FILE" || echo "OPENAI_TTS_MODEL=gpt-4o-mini-tts" >> "$ENV_FILE"
grep -qE '^OPENAI_TTS_VOICE=' "$ENV_FILE" || echo "OPENAI_TTS_VOICE=alloy" >> "$ENV_FILE"

# Feedback (masked)
LEN=${#KEY}
HEAD=${KEY:0:6}
TAIL=${KEY: -4}
echo "Saved to $ENV_FILE: length=$LEN, begins '${HEAD}' … ends '${TAIL}'"
echo "Start the server with ./run_falkland.sh"
echo "Tip: trigger a radio line, then check /api/status for audio.radio.file"
