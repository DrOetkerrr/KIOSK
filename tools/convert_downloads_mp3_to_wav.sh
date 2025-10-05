#!/usr/bin/env bash
# Convert every .mp3 file in a downloads directory to .wav using ffmpeg.
# Usage: ./convert_downloads_mp3_to_wav.sh [directory]
# Directory defaults to the macOS Downloads folder (~/Downloads).

set -euo pipefail

DOWNLOAD_DIR="$HOME/Downloads"
if [ "${1:-}" != "" ]; then
  DOWNLOAD_DIR="$1"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Error: ffmpeg is not installed or not on PATH." >&2
  exit 1
fi

if [ ! -d "$DOWNLOAD_DIR" ]; then
  echo "Error: directory '$DOWNLOAD_DIR' does not exist." >&2
  exit 1
fi

found=0
while IFS= read -r -d '' mp3; do
  found=1
  wav="${mp3%.*}.wav"
  echo "Converting: $mp3 -> $wav"
  ffmpeg -y -loglevel error -i "$mp3" "$wav"
  echo "Done: $wav"
  echo
done < <(find "$DOWNLOAD_DIR" -type f -iname '*.mp3' -print0)

if [ "$found" -eq 0 ]; then
  echo "No .mp3 files found in '$DOWNLOAD_DIR'."
  exit 0
fi

echo "Conversion complete."
