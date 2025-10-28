# Falkland V2 → V3 Gameplay Inventory

Snapshot of the current Falkland V2 behaviour we must reproduce when rebuilding cleanly for Falkland V3.

## Core Simulation
- Ship navigation uses a 40x40 world grid with a 26x26 bridge board; heading 0° = north, tick loop advances vessel position (`core/engine.py`, `runtime_service.py`).
- Radar simulates contacts with randomisation, wave schedules, and mission-aware filters. Radar contacts feed both UI and CAP logic (`radar.py`, `runtime_radar.py`).
- CAP (Hermes) orchestrator launches, tracks, and recovers sorties; hooks drive damage application and contact removal on successful hits (`subsystems/hermes_cap.py`, `runtime_cap.py`).
- Mission controller evaluates AND/OR success and failure trees from JSON definitions, emits events, and synchronises settings with CAP and audio cues (`subsystems/mission.py`, `runtime_mission.py`).
- Audio coordinator plays scripted voice lines, alarms, and mission callouts; ties into filesystem assets and `/api/status` audio payloads (`runtime_audio.py`, `subsystems/audio.py`).

## Runtime & Persistence
- `GameRuntime` centralises engine, radar, mission, CAP, and audio state; replaces prior global singletons and gates access behind locks (`runtime_service.py`).
- `StateRepository` exposes canonical file paths for logs, flight recorder, arming state, crew, health, roadmap, and voice events while providing JSON helpers (`runtime_state.py`).
- Wave schedules, mission configs, contacts, crew, and weapon catalogues live under `data/` and `state/`; runtime mutates these to track progress.
- Watchdog records `/api/status` poll cadence, alerts when UI stalls, and writes structured log entries (`watchdog.py`, `webdash.py:StatusPollWatchdog`).

## APIs & Integrations
- Flask app in `webdash.py` exposes `/api/status` along with domain routes for navigation, radar, CAP, mission, comms, diagnostics, resupply, and weapons (`routes/` package).
- `/api/status` aggregates engine snapshot, radar contacts, mission state, CAP posture, audio directives, alarms, roadmap, and power grid data (`subsystems/status.py`).
- Desktop shell and CLI smoke probes reuse the same HTTP surface and polls to validate behaviour (`desktop_app.py`, `tools/verify_*.sh`).
- Event bus behaviour is ad hoc today: subsystems call into `core/webcore` functions that append to log files, emit voice events, or trigger missions.

## Front-End Stations
- Legacy Stations UI (`static/stations/main.js`) drives five consoles: `NAV`, `RADAR`, `WPN`, `RADIO`, and `ENG`, each updating from shared `/api/status` payloads.
- Stations poll every 1.5–12 seconds with exponential backoff, surface connection status banners, and trigger audio playback based on `audio` payload fields.
- UI modules transform backend payloads into station-specific panels (radar plot, navigation orders, CAP launch state, weapons arming, radio transcripts).
- Auxiliary web UI (`static/app.js`, `templates/`) provides commander/overview consoles with similar data dependencies.

## External Behaviour Expectations
- Gameplay loop includes mission progression, CAP engagements removing radar contacts, alarms for threats, weapon arming/launch flows, and voice/radio playback.
- Filesystem side effects: flight recorder JSONL, crew roster updates, mission outcome logging, audio cue playback, and wave schedule sync.
- Non-functional: requests must remain responsive (watchdog thresholds), UI polling cannot break, and existing tests under `tests/` verify CAP engagements and smoke scenarios.

This inventory defines the contract V3 must meet before we can retire the legacy code.
