#!/usr/bin/env bash
set -euo pipefail

MAC="2E:39:97:18:B8:E4"

echo "[FOSI] Ensuring Bluetooth service is up..."
systemctl --user restart pipewire wireplumber || true
sudo systemctl restart bluetooth

echo "[FOSI] Trying to pair/trust/connect $MAC"
bluetoothctl <<EOF
power on
agent on
default-agent
pair $MAC
EOF

sleep 2
bluetoothctl trust $MAC
sleep 1
bluetoothctl connect $MAC
sleep 2

# Set as default sink if available
SINK=$(pactl list short sinks | awk -v mac="$MAC" '$2 ~ mac {print $2; exit}')
if [ -n "$SINK" ]; then
  echo "[FOSI] Found sink: $SINK"
  pactl set-default-sink "$SINK"
  paplay --device="$SINK" /usr/share/sounds/alsa/Front_Center.wav || true
else
  echo "[FOSI] No sink found — check amp LED."
fi