# projects/falklands/run_bridge.py
from __future__ import annotations
import os, sys, time, tempfile, io, signal
from pathlib import Path
from typing import List

import numpy as np
import sounddevice as sd
import soundfile as sf
from openai import OpenAI

# --- project imports
from projects.falklands.core.engine import Engine
from projects.falklands.llm_intent import EnsignLLM, extract_commands, strip_commands_from
from projects.falklands.utils.audio import (
    PlaybackQueue, speak_radio, captain_ptt_start, captain_ptt_stop
)

# -------- settings you can tweak --------
CAPTURE_SR = 48_000
CAPTURE_SECONDS = 3.0          # how long a press-to-talk capture lasts (ENTER-based)
ASR_MODEL = "gpt-4o-transcribe"
ASR_LANG  = os.environ.get("ENSIGN_LANG", "en")  # set ENSIGN_LANG=nl if you want Dutch
TTS_VOICE = os.environ.get("ENSIGN_VOICE", "ash")

STATE_PATH = Path.home() / "kiosk" / "falklands_state.json"

client = OpenAI()

def record_block(seconds: float = CAPTURE_SECONDS, sr: int = CAPTURE_SR) -> Path:
    """Capture mono PCM from default input for `seconds` and return a temp wav path."""
    frames = int(seconds * sr)
    print(f"[REC] Recording… press ENTER again to stop." if seconds < 1e-6 else f"[REC] Recording {seconds:.1f}s…")
    audio = sd.rec(frames, samplerate=sr, channels=1, dtype="int16")
    sd.wait()
    tmp = Path(tempfile.mktemp(suffix=".wav"))
    sf.write(str(tmp), audio, sr, subtype="PCM_16")
    print(f"[REC] Saved: {tmp}")
    return tmp

def transcribe_wav(path: Path, language: str = ASR_LANG) -> str:
    """Use OpenAI ASR; keep language explicit to avoid the 'auto' error."""
    with open(path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model=ASR_MODEL,
            file=f,
            language=language,
        )
    return (resp.text or "").strip()

def main():
    print("Starting Falklands bridge (SPACE/ENTER to start/stop). Ctrl+C to exit.")
    print("Press ENTER to talk, ENTER again to stop.")

    # Engine + audio queue
    eng = Engine(state_path=STATE_PATH)
    pq = PlaybackQueue()
    pq.start()

    # LLM brain
    ensign = EnsignLLM()

    # gentle HUD heartbeat, so you still see state changing
    last_hud = ""
    last_tick = time.monotonic()

    def shutdown(*_):
        print("Shutting down…")
        pq.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    while True:
        # 1) tick the simulation at ~1 Hz
        now = time.monotonic()
        dt = now - last_tick
        if dt >= 1.0:
            eng.tick(dt)
            last_tick = now
            hud = eng.hud_line()
            if hud != last_hud:
                print("[HUD]", hud)
                last_hud = hud

        # 2) simple ENTER-to-capture loop
        try:
            line = input()  # wait for ENTER
        except EOFError:
            shutdown()

        # PTT start tone (captain)
        captain_ptt_start(pq)

        # capture until next ENTER (toggle), or just fixed 3s if stdin is not interactive
        # Here we do a fixed-length capture to keep it simple and robust.
        wav_in = record_block(CAPTURE_SECONDS, CAPTURE_SR)

        # PTT stop tone (captain)
        captain_ptt_stop(pq)

        # 3) ASR
        try:
            text = transcribe_wav(wav_in, ASR_LANG)
        except Exception as e:
            print(f"[ERR] ASR failed: {e}")
            continue

        print(f"[ASR] -> {text}")
        if not text:
            continue

        # 4) LLM intent → (assistant_text, /commands …)
        alerts = getattr(eng, "get_alerts", None)
        if callable(alerts):
            alerts_list = alerts()
        else:
            alerts_list = getattr(eng, "alerts", []) or []

        try:
            assistant_text, _cmds = ensign.respond(text, eng.public_state(), alerts_list)
        except Exception as e:
            print(f"[ERR] LLM failed: {e}")
            continue

        # 5) speak the radio reply (without the commands block)
        to_speak = strip_commands_from(assistant_text)
        if to_speak:
            print("ENSIGN:", to_speak)
            speak_radio(pq, to_speak, voice=TTS_VOICE)

        # 6) execute commands (if any)
        cmds: List[str] = extract_commands(assistant_text)
        for c in cmds:
            print("[EXEC]", c)
            try:
                out = eng.exec_slash(c)
            except Exception as e:
                out = f"ERR executing '{c}': {e}"
            if out:
                print(out)

        # loop back for next ENTER press

if __name__ == "__main__":
    main()