# Falkland V2 — Refactor/Stabilization Progress

This file captures what’s done, how to resume, and next steps. It’s safe to commit and update incrementally.

## What’s Done
- Stability: fixed officer_say usage in /api/command; completed /api/reset; dashboard null-guarded.
- Dev helpers: make check (compile + route count + size guard), optional pre‑push hook.
- Runner: projects/FalklandV2/run_webdash.sh.
- UI: extracted inline JS to /static/app.js; disabled leftover inline block in templates/index.html; sound.js included for audio cues.
- API split into blueprints (no behavior change):
  - routes/command.py → /api/command
  - routes/radar.py → /radar/* + /debug/cellmap
  - routes/radar_dev.py → moved dev endpoints: /radar/force_spawn*, /radar/spawn_by_name, /radar/reload_catalog, /debug/*
  - routes/weapons.py → /weapons/* (removed legacy /__old/weapons/* placeholders)
  - routes/cap.py → /cap/* (moved CAP routes from webdash)
  - routes/radio.py → /radio/say, /radio/ask (moved radio routes from webdash)
- Removed legacy: deleted `projects/falklandV2/api.py` and `templates/index.html.bak`.
- Dashboard cleanup: moved Self Test button to Menu → Diagnostics.
- Added Diagnostics: `/diag/reset`, session markers `/session/start`, `/session/end` (+ UI in Menu).
- Debrief tool: `tools/debrief_session.py` + `make debrief` to summarize sessions from `logs/flight.jsonl`.
- Runtime backbone: introduced `GameRuntime` service (`projects/falklandV2/runtime_service.py`) and bound `webdash` globals to it; `/diag/reset` now rebuilds engine/CAP via the runtime.
- Desktop shell (alpha): added PySide6 UI `projects/falklandV2/desktop_app.py` + `make desktop` that reuses the existing HTTP API on localhost; shows HUD, Radar contacts, and Weapons with Arm/Test/Fire.

## How To Resume
- Start server: `PORT=5055 make start`
- Quick verify: `PORT=5055 bash tools/verify_suite.sh` (report → logs/verify_*.md)
- Static checks: `make check`
- Desktop (alpha): `make desktop` (starts server on `PORT` if needed, launches Qt UI). Set `FULLSCREEN=1` for kiosk panel.
- Lock flow: `curl -sS 'http://127.0.0.1:5055/api/command?cmd=/radar%20lock%20nearest'`
- Radar sanity: `curl -sS 'http://127.0.0.1:5055/radar/force_spawn_near?class=Aircraft&range=2.5'`
- Session debrief:
  - Tag: `export KIOSK_SESSION_ID=$(date +%Y%m%d_%H%M%S)` (optional)
  - Play, then: `make debrief` (use `SESSION=$KIOSK_SESSION_ID` and/or `LAST=45m` to filter)
  - Report: `logs/debrief_*.md`

## Next Small Steps (pick one)
1) Case hygiene: normalize imports to projects/falklandV2 (avoid case‑sensitivity pitfalls).
2) Continue slimming webdash.py by moving any remaining route clusters.
3) Trim `templates/index.html` inline disabled script block now that `/static/app.js` is authoritative.
4) Move radio AI + alarm routes to blueprints (reduce webdash size).

## Savepoints
- Use rescue tags before changes: `git tag rescue-$(date +%Y%m%d_%H%M%S)` then `git push --tags`.
- Recent tags: `rescue-main-*`, `rescue-rollback-*` (already pushed).

## Notes
- make check warns if files get too large. webdash.py is still big by design; we’re reducing it in small slices.
## Stabilization Contract (agreed)
- Focus: rebuild stability first; no gameplay debugging until done.
- Allowed checks: build, health, routes, status schema, logs (no ad‑hoc gameplay tests).
- Goal: single module state, clean imports, one UI JS source, predictable `/api/status` and routes.
- Done signal: I will report “Stabilization complete” with acceptance checklist results; then we switch to gameplay bug‑hunting.

- Optional: enable pre-push guard locally: `ln -s ../../tools/hooks/pre-push.sample .git/hooks/pre-push`.
