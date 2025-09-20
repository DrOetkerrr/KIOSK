# Voice Linkup Quickstart

Endpoints (server must be running):

- Preview intent as JSON (no audio, no execution):
  curl -sS -X POST "http://127.0.0.1:${PORT:-5055}/radio/interp_ai" \
       -H 'Content-Type: application/json' \
       -d '{"text":"Scan radar and lock nearest hostile"}' | jq

- Voice chain: audio → ASR → LLM plan → validation (no execution):
  curl -sS -X POST "http://127.0.0.1:${PORT:-5055}/radio/voice?speak=1&voice_role=Radar" \
       -F file=@sample.wav | jq

  Notes:
  - Set speak=1 to queue the parsed radio line for TTS playback.
  - Use voice_role to pick a crew voice (e.g., Radar, Weapons, Bridge).
  - Response includes `.ai.parsed`, `.ai.validation`, and `.affirm` for `/radio/exec`.
  - UI shortcut: Hold the PTT button (left of WPN) or hold the Spacebar to record mic and send to `/radio/voice?speak=1&voice_role=Weapons`.
  - Easiest (no tools): open http://127.0.0.1:${PORT:-5055}/radio/test, type your line, click “Speak reply”.

- Execute a validated plan (confirm required for risky actions):
  curl -sS -X POST "http://127.0.0.1:${PORT:-5055}/radio/exec" \
       -H 'Content-Type: application/json' \
       -d '{"plan": { ... from /radio/voice .ai.parsed ... }, "confirm": true}' | jq

- Execute and speak the plan's radio line (one call):
  curl -sS -X POST "http://127.0.0.1:${PORT:-5055}/radio/exec?voice_role=Bridge&speak=1" \
       -H 'Content-Type: application/json' \
       -d '{"plan": { ... }, "confirm": true}' | jq

- Speak any radio line (TTS queue):
  curl -sS -X POST "http://127.0.0.1:${PORT:-5055}/radio/say" \
       -H 'Content-Type: application/json' \
       -d '{"kind":"Radar","text":"Captain, scanning radar."}' | jq

Environment:
- OPENAI_API_KEY           # required for ASR and LLM JSON mode (and OpenAI TTS fallback)
- OPENAI_ASR_MODEL=whisper-1            # optional
- OPENAI_INTERP_MODEL=gpt-4o-mini       # optional
- OPENAI_TTS_MODEL=gpt-4o-mini-tts      # optional
- OPENAI_TTS_VOICE=alloy                # optional (when TTS_PROVIDER not piper)
- TTS_PROVIDER=openai|piper|macos       # default openai unless overridden

Tip: Use tools/install_openai_key.sh to put OPENAI_API_KEY into .env.

Run it with bash (the script isn’t executable by default):

  bash tools/install_openai_key.sh --key 'sk-…' -y

Or set it manually in repo root:

  echo "OPENAI_API_KEY=sk-…" >> .env

Restart the server with ./run_falkland.sh so it sources .env.

Disable bridge ambience (for testing)
- Append `?nobridge=1` to the Stations URL, or in DevTools run:
  localStorage.setItem('DISABLE_BRIDGE','1'); location.reload()
- To re‑enable, clear the flag:
  localStorage.removeItem('DISABLE_BRIDGE'); location.reload()

Common setup error (latin-1 / “…”)
- If you see an error mentioning "'latin-1' codec can't encode character '\u2026'":
  - It usually means your OPENAI_API_KEY contains a fancy ellipsis (…)
    or smart quotes copied from documentation.
  - Fix: open `.env` and paste your real key exactly as given (ASCII only), e.g.
      OPENAI_API_KEY=sk-1234abcd... (no … or quotes)
  - Then restart with `./run_falkland.sh`.
