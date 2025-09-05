#!/usr/bin/env python3
"""
voice_ensign.py — Toggle Push-to-Talk + proactive alerts
SPACE to start; SPACE again to stop. Ensign will also interrupt with voice alerts
whenever radar raises one (inside 10 NM etc.).

- Robust over SSH.
- Uses gpt-4o-mini-transcribe and gpt-4o-mini-tts (response_format='wav').
"""

from __future__ import annotations
from pathlib import Path
import tempfile
import subprocess
import sys
import termios
import tty
import select
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
from openai import OpenAI

from falklands.core.engine import Engine

VOICE = "ash"
MODEL_TRANSCRIBE = "gpt-4o-mini-transcribe"
MODEL_CHAT = "gpt-4.1-mini"
DEVICE_NAME_HINT = "Tula"
APLAY_BIN = "aplay"
CANDIDATE_RATES = [48000, 44100, 32000, 16000]
MAX_RECORD_S = 120
LATENCY = "high"
BLOCKSIZE = 1024

client = OpenAI()

class RawKeyReader:
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        return self
    def __exit__(self, exc_type, exc, tb):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
    def poll(self, timeout_ms: int) -> str | None:
        r, _, _ = select.select([sys.stdin], [], [], timeout_ms/1000.0)
        if r:
            b = sys.stdin.buffer.read(1)
            return b.decode(errors="ignore")
        return None

def pick_input_device(name_hint: str | None = None) -> int | None:
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"[ERR] sd.query_devices failed: {e}")
        return None
    chosen = None
    if name_hint:
        for i, d in enumerate(devices):
            if d.get("max_input_channels",0)>0 and name_hint.lower() in d.get("name","").lower():
                chosen = i; break
    if chosen is None:
        for i, d in enumerate(devices):
            if d.get("max_input_channels",0)>0:
                chosen = i; break
    if chosen is not None:
        print(f"[INFO] Using input device {chosen}: {devices[chosen].get('name','?')}")
    else:
        print("[ERR] No capture device found.")
    return chosen

def find_working_rate(device_idx: int | None) -> int:
    for sr in CANDIDATE_RATES:
        try:
            sd.check_input_settings(device=device_idx, samplerate=sr, channels=1, dtype="int16")
            print(f"[INFO] Mic accepts {sr} Hz"); return sr
        except Exception: pass
    print("[WARN] Falling back to device default rate."); return 0

class ToggleRecorder:
    def __init__(self, device_idx: int | None, samplerate: int | None):
        self.device_idx = device_idx
        self.samplerate = samplerate or None
        self.chunks: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.start_ts = 0.0
    def _cb(self, indata, frames, time_info, status):
        if status: pass
        self.chunks.append(indata.copy())
    def start(self):
        self.chunks.clear(); self.start_ts = time.time()
        self.stream = sd.InputStream(
            samplerate=self.samplerate, channels=1, dtype="int16",
            device=self.device_idx, callback=self._cb,
            blocksize=BLOCKSIZE, latency=LATENCY,
        )
        self.stream.start()
        sr = int(self.samplerate or sd.query_devices(self.device_idx)["default_samplerate"])
        print(f"[REC] Recording… (sr={sr})  [SPACE again to stop]")
    def stop_and_save(self) -> str | None:
        if not self.stream: return None
        self.stream.stop(); self.stream.close(); self.stream = None
        dur = time.time() - self.start_ts
        if dur <= 0.15 or not self.chunks:
            print("[REC] Too short / no audio captured."); return None
        audio = np.concatenate(self.chunks, axis=0)
        actual_sr = int(self.samplerate or sd.query_devices(self.device_idx)["default_samplerate"])
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, audio, actual_sr)
        print(f"[REC] Saved {dur:.2f}s to {tmp.name}")
        return tmp.name

def transcribe(path: str) -> str:
    print("[ASR] Transcribing…")
    with open(path, "rb") as f:
        resp = client.audio.transcriptions.create(model=MODEL_TRANSCRIBE, file=f)
    text = (resp.text or "").strip()
    print(f"[ASR] -> {text!r}")
    return text

def tts_to_wav(text: str) -> str:
    print("[TTS] Synthesizing reply…")
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    speech = client.audio.speech.create(
        model="gpt-4o-mini-tts", voice=VOICE, input=text, response_format="wav",
    )
    data = speech.read() if hasattr(speech, "read") else speech
    with open(out.name, "wb") as w:
        if isinstance(data, (bytes, bytearray)): w.write(data)
        else: w.write(data.read())
    print(f"[TTS] Saved to {out.name}")
    return out.name

def play_wav(path: str):
    print("[PLAY] Playing reply…")
    try: subprocess.run([APLAY_BIN, path], check=True)
    except FileNotFoundError: print("[WARN] Install ALSA: sudo apt-get install -y alsa-utils")
    except subprocess.CalledProcessError as e: print(f"[ERR] aplay failed: {e}")

def main():
    state_path = Path.home() / "kiosk" / "state_falklands.json"
    eng = Engine(state_path=state_path, model=MODEL_CHAT)

    in_dev = pick_input_device(DEVICE_NAME_HINT or None)
    sr = find_working_rate(in_dev)
    recorder = ToggleRecorder(in_dev, sr)
    rec_active = False
    deferred_alerts: list[str] = []

    print("\n=== Voice Ensign (Toggle PTT + Alerts) ===")
    print("SPACE: start/stop recording. 'q': quit.\n")

    def speak_alerts(queue_first=True):
        # drain queued alerts (from engine + deferred while recording)
        msgs: list[str] = []
        if queue_first:
            while True:
                m = eng.pop_alert()
                if not m: break
                msgs.append(m)
        if deferred_alerts:
            msgs.extend(deferred_alerts)
            deferred_alerts.clear()
        for msg in msgs:
            print(f"NPC ALERT: {msg}")
            try:
                wav = tts_to_wav(msg)
                play_wav(wav)
            except Exception as e:
                print(f"[ERR] TTS/playback failed: {e}")

    with RawKeyReader() as kb:
        last_idle_alert_check = 0.0
        while True:
            # Idle-time alert speaking (every ~0.3s)
            now = time.time()
            if not rec_active and (now - last_idle_alert_check) > 0.3:
                speak_alerts(queue_first=True)
                last_idle_alert_check = now

            ch = kb.poll(timeout_ms=100)
            if ch is None:
                continue
            if ch.lower() == 'q':
                if rec_active:
                    print("\n[INFO] Aborting recording.")
                print("Bye.")
                break
            if ch == ' ':
                if not rec_active:
                    rec_active = True
                    recorder.start()
                else:
                    rec_active = False
                    wav_in = recorder.stop_and_save()
                    if not wav_in: continue
                    txt = transcribe(wav_in)
                    if not txt:
                        print("[ASR] (empty) — try again.")
                        continue
                    print(f"You: {txt}")
                    reply = eng.ask(txt)
                    print("NPC:\n" + reply)
                    try:
                        wav_out = tts_to_wav(reply)
                        play_wav(wav_out)
                    except Exception as e:
                        print(f"[ERR] TTS/playback failed: {e}")
                    # speak any queued alerts right after the reply
                    speak_alerts(queue_first=True)
            else:
                # any other key while recording: stop & process quickly
                if rec_active:
                    rec_active = False
                    wav_in = recorder.stop_and_save()
                    if not wav_in: continue
                    txt = transcribe(wav_in)
                    print(f"You: {txt}")
                    reply = eng.ask(txt)
                    print("NPC:\n" + reply)
                    try:
                        wav_out = tts_to_wav(reply); play_wav(wav_out)
                    except Exception as e:
                        print(f"[ERR] TTS/playback failed: {e}")
                    speak_alerts(queue_first=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")