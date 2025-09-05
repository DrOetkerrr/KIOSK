#!/usr/bin/env bash
set -euo pipefail

IN="${1:-squelch.m4a}"     # your trimmed file; change name if needed
OFF_MS="${2:-250}"         # tail length (ms)
ON_MS="${3:-120}"          # key-up burst length (ms)

if [ ! -f "$IN" ]; then
  echo "Input not found: $IN"
  echo "Here are audio files in this folder:"
  ls -1 *.m4a *.mp3 *.wav 2>/dev/null || true
  exit 1
fi

# Convert your tail to 24 kHz mono WAV and trim to OFF_MS
ffmpeg -y -loglevel error -i "$IN" -ac 1 -ar 24000 -t "$(awk "BEGIN{print $OFF_MS/1000}")" radio_off.raw.wav

# Normalize and band-limit (300–3000 Hz) so it sounds like radio squelch
sox -q radio_off.raw.wav radio_off.wav gain -n 3 highpass 300 lowpass 3000
rm -f radio_off.raw.wav
echo "[OK] radio_off.wav made (${OFF_MS} ms)."

# Synthesize key-up burst (brown noise), band-limit and quick fade
sox -n -r 24000 -c 1 radio_on.wav synth "$(awk "BEGIN{print $ON_MS/1000}")" brownnoise vol 0.4 highpass 300 lowpass 3000 fade t 0 0.12 0.02
echo "[OK] radio_on.wav made (${ON_MS} ms)."

# Quick durations
soxi -D radio_on.wav radio_off.wav | awk '{printf "[DUR] %ss\n",$0}'
