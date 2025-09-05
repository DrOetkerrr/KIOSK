#!/usr/bin/env bash
# Bring up BT speaker + mic on the Pi after reboot
set -euo pipefail

# === CONFIG: set your amp's MAC here ===
AMP_MAC="${AMP_MAC:-2E:39:97:18:B8:E4}"   # Wuzhi/Fosi MAC
TEST_WAV="/usr/share/sounds/alsa/Front_Center.wav"

say(){ echo -e "\033[1;36m$*\033[0m"; }

say "[1/6] Restarting audio session (PipeWire + WirePlumber)…"
systemctl --user restart pipewire >/dev/null 2>&1 || true
systemctl --user restart wireplumber >/dev/null 2>&1 || true
sleep 1

say "[2/6] Powering on Bluetooth + trusting amp…"
bluetoothctl <<BT
power on
agent on
default-agent
trust $AMP_MAC
BT

say "[3/6] Connecting to amp ($AMP_MAC)…"
# Try connect; if it fails, try pair+connect
if ! bluetoothctl connect "$AMP_MAC" | grep -qi "successful"; then
  say "   Initial connect failed; attempting pair then connect…"
  bluetoothctl <<BT
remove $AMP_MAC
scan on
BT
  sleep 4
  bluetoothctl <<BT
pair $AMP_MAC
trust $AMP_MAC
connect $AMP_MAC
BT
fi
sleep 1

say "[4/6] Locating A2DP sink…"
SINK=$(pactl list short sinks | awk '/bluez_output/ {print $2; exit}')
if [[ -z "${SINK:-}" ]]; then
  echo "!! No bluez_output sink found. Amp may still be blinking / not in A2DP. Try power-cycling the amp and re-run."
  pactl list short sinks
  exit 2
fi
say "    Found sink: $SINK"

say "[5/6] Making BT sink default, unmuting, setting volume…"
pactl set-default-sink "$SINK" || true
pactl set-sink-mute   "$SINK" 0 || true
pactl set-sink-volume "$SINK" 100% || true

say "[6/6] Testing playback…"
paplay --device="$SINK" "$TEST_WAV" || true

# Optional: set default USB mic if present
MIC_SRC=$(pactl list short sources | awk '/alsa_input.*usb/ {print $2; exit}')
if [[ -n "${MIC_SRC:-}" ]]; then
  say "    Setting default source to USB mic: $MIC_SRC"
  pactl set-default-source "$MIC_SRC" || true
fi

echo
say "Done. Suggested for this shell:"
echo "  export PULSE_SINK=\"$SINK\""
echo
say "Quick checks:"
echo "  arecord -l            # mic cards"
echo "  pactl list short sinks  # should show $SINK"
