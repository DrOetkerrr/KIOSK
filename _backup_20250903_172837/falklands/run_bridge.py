# projects/falklands/run_bridge.py
"""
Bridge loop for Falklands:
- SPACE = push-to-talk (hold to record, release to send)
- GPT (Ensign) interprets intent and may emit /commands in a [COMMANDS] block
- Engine executes /commands, HUD prints, radio FX wraps TTS playback

Dependencies in venv:
  pip install openai sounddevice soundfile pynput

Audio assumptions:
- Input: your Tula mic (set CAPTURE_DEVICE_IDX below if needed)
- Output: Bluetooth sink already default (use your fosi_connect.sh if needed)
- Radio FX wavs in this folder: radio_on.wav, radio_off.wav (optional)

Env knobs (export before running if desired):
  PTT_PREWAIT_MS (default 120)  small delay before capture
  PTT_TRIM_MS    (default 40)   trim from start to avoid key thumps
  PTT_MAX_SEC    (default 6.0)  safety cap on recording
  DEFAULT_VOICE  (default "ash") OpenAI TTS voice
"""

import os, sys, time, io, math, threading, queue, re, json
from pathlib import Path
from typing import List, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from openai import OpenAI

# --- Game engine imports ---
from projects.falklands.core.engine import Engine

# --- LLM intent module (we built this already) ---
from projects.falklands.intent_cli import extract_commands  # reuse the same extractor
from projects.falklands.llm_intent import EnsignLLM

# ---------------- Config ----------------
SAMPLE_RATE = 48000
CAPTURE_DEVICE_IDX = None  # set integer card index if needed, else None = default
DEFAULT_TTS_VOICE = os.environ.get("DEFAULT_VOICE", "ash")

PTT_PREWAIT_MS = int(os.environ.get("PTT_PREWAIT_MS", "120"))
PTT_TRIM_MS    = int(os.environ.get("PTT_TRIM_MS", "40"))
PTT_MAX_SEC    = float(os.environ.get("PTT_MAX_SEC", "6.0"))

RADIO_ON_WAV  = Path(__file__).with_name("radio_on.wav")
RADIO_OFF_WAV = Path(__file__).with_name("radio_off.wav")

# ---------------- Helpers ----------------
client = OpenAI()
ensign = EnsignLLM(model="gpt-4.1-mini", temperature=0.3)

def hud_line(eng: Engine) -> str:
    st = eng.public_state()
    ship = st.get("ship", {})
    pri  = st.get("primary")
    ship_str = f"Ship {ship.get('col',50)}-{ship.get('row',50)} | hdg {ship.get('heading',270)}° spd {ship.get('speed',15)} kn"
    pri_str = "No active contact"
    if pri:
        clock = pri.get("clock", "?")
        rng   = pri.get("range_nm", "?")
        grid  = pri.get("grid", "??-??")
        typ   = pri.get("type","?")
        pri_str = f"Primary {typ} at {clock}, {rng:.1f} NM, grid {grid}"
    return f"[HUD] {ship_str} || {pri_str}"

def play_wav(path: Path):
    if not path.exists():
        return
    data, sr = sf.read(str(path), dtype="int16")
    sd.play(data, sr)
    sd.wait()

def tts_to_wav_bytes(text: str, voice: str = DEFAULT_TTS_VOICE) -> bytes:
    # Minimal, reliable TTS call
    resp = client.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=text,
        format="wav"
    )
    return resp.read()

def say_radio(text: str):
    """Play radio_on + TTS + radio_off, with tiny padding to avoid clipping the first syllable."""
    # small padding of silence
    pad_ms = 120
    if RADIO_ON_WAV.exists():
        play_wav(RADIO_ON_WAV)
    wav_bytes = tts_to_wav_bytes(text, DEFAULT_TTS_VOICE)
    data, sr = sf.read(io.BytesIO(wav_bytes), dtype="int16")
    if pad_ms > 0:
        pad = np.zeros(int(sr * pad_ms / 1000), dtype=np.int16)
        data = np.concatenate([pad, data], axis=0)
    sd.play(data, sr)
    sd.wait()
    if RADIO_OFF_WAV.exists():
        play_wav(RADIO_OFF_WAV)

# --------------- Push-to-talk ---------------
class SpacePTT:
    """
    Terminal-based PTT: hold SPACE to record, release to stop.
    Uses non-blocking keyboard read via sys.stdin.
    Keep terminal focused for this to work.
    """
    def __init__(self):
        import termios, tty
        self.term_fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(self.term_fd)
        tty.setcbreak(self.term_fd)
        self._restore_pushed = False

    def restore(self):
        if not self._restore_pushed:
            import termios
            termios.tcsetattr(self.term_fd, termios.TCSADRAIN, self.old_settings)
            self._restore_pushed = True

    def is_space_pressed(self) -> bool:
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            # echo minimal key info
            if ch == " ":
                return True
        return False

def record_until_space_release(max_seconds: float = PTT_MAX_SEC) -> np.ndarray:
    """
    Start capture when SPACE is first detected, stop when key released or max_seconds reached.
    Adds a small prewait before opening device to avoid on_start pops.
    """
    start = time.time()
    # Wait for initial SPACE press
    while True:
        if time.time() - start > 30:
            return np.array([], dtype=np.int16)
        import select
        if select.select([sys.stdin], [], [], 0.01)[0]:
            ch = sys.stdin.read(1)
            if ch == " ":
                break

    if PTT_PREWAIT_MS > 0:
        time.sleep(PTT_PREWAIT_MS / 1000)

    # Record while space is *held*; naive approach: sample until next non-space keystroke
    buf = []
    block = int(SAMPLE_RATE * 0.05)  # 50 ms blocks
    rec = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=CAPTURE_DEVICE_IDX)
    rec.start()
    t0 = time.time()
    try:
        while time.time() - t0 < max_seconds:
            # non-blocking read of stdin—if another char arrives, that's the release
            import select
            if select.select([sys.stdin], [], [], 0)[0]:
                k = sys.stdin.read(1)
                if k != " ":
                    break
            frames, _ = rec.read(block)
            buf.append(frames.copy())
    finally:
        rec.stop(); rec.close()
    if not buf:
        return np.array([], dtype=np.int16)
    audio = np.concatenate(buf, axis=0).reshape(-1)

    # Trim leading transient if requested
    if PTT_TRIM_MS > 0:
        trim = int(SAMPLE_RATE * PTT_TRIM_MS / 1000)
        if len(audio) > trim:
            audio = audio[trim:]
    return audio

def asr_whisper(audio_int16: np.ndarray) -> str:
    if audio_int16.size == 0:
        return ""
    # write to bytes wav
    bio = io.BytesIO()
    sf.write(bio, audio_int16, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    bio.seek(0)
    txt = client.audio.transcriptions.create(
        model="whisper-1",
        file=("speech.wav", bio.read(), "audio/wav"),
        response_format="text",
        language="en"
    )
    return (txt or "").strip()

# --------------- Main loop ---------------
def main():
    print("Starting Falklands bridge (LLM intent).")
    eng = Engine(state_path=Path.home() / "kiosk" / "falklands_state.json")

    print(hud_line(eng))
    print("Hold SPACE to talk. Release to send. Ctrl+C to quit.")

    try:
        while True:
            audio = record_until_space_release()
            if audio.size == 0:
                # idle tick for radar/contacts, and update HUD
                eng.tick(dt=2.0)
                print(hud_line(eng))
                continue

            user_text = asr_whisper(audio)
            if not user_text:
                continue

            print(f"[ASR] -> {user_text}")
            # LLM interprets intent & persona
            assistant_text, cmds = ensign.respond(
                user_text=user_text,
                state=eng.public_state(),
                last_alerts=eng.last_alerts()
            )

            # Speak radio reply
            say_radio(assistant_text)

            # Execute suggested commands
            for c in cmds:
                print(f"[Executed] {c}")
                try:
                    out = eng.exec_command(c)
                    if out:
                        print(out.strip())
                except Exception as e:
                    print(f"[ERR] {e}")

            # advance world a little each exchange
            eng.tick(dt=2.0)
            print(hud_line(eng))

    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        try:
            sd.stop()
        except Exception:
            pass

if __name__ == "__main__":
    main()