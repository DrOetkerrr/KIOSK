#!/usr/bin/env bash
set -euo pipefail
IN="${1:-whitestatic.mp3}"   # source file
OFFSET="${2:-0.20}"          # seconds into file to start (skip any silence)
DUR="${3:-0.12}"             # burst length in seconds
CHAN="${4:-L}"               # L or R

if [ ! -f "$IN" ]; then
  echo "Input not found: $IN"; exit 1
fi

# pick one channel (avoid phase-cancel silence), resample to 24 kHz mono
MAP="0.0.0"; [ "$CHAN" = "R" ] && MAP="0.0.1"
ffmpeg -y -loglevel error -i "$IN" -map_channel "$MAP" -ac 1 -ar 24000 tmp_white_mono.wav

# normalize and radio band-limit (300–3000 Hz)
sox -q tmp_white_mono.wav tmp_white_nb.wav gain -n -1 highpass 300 lowpass 3000

# cut the burst
sox -q tmp_white_nb.wav radio_on.wav trim "$OFFSET" "$DUR"

rm -f tmp_white_mono.wav tmp_white_nb.wav
echo "[OK] radio_on.wav made from $IN (chan=$CHAN, offset=${OFFSET}s, dur=${DUR}s)"
