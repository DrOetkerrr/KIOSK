# Falkland V2 — Refactor/Stabilization Progress

This file captures what’s done, how to resume, and next steps. It’s safe to commit and update incrementally.

## What’s Done
- Stability: fixed officer_say usage in /api/command; completed /api/reset; dashboard null-guarded.
- Dev helpers: make check (compile + route count + size guard), optional pre‑push hook.
- Runner: projects/FalklandV2/run_webdash.sh.
- UI: extracted inline JS to /static/app.js (no behavior change).
- API split into blueprints (no behavior change):
  - routes/command.py → /api/command
  - routes/radar.py → /radar/* + /debug/cellmap
  - routes/weapons.py → /weapons/* (old inline routes are temporarily mapped under /__old/weapons/*)

## How To Resume
- Start server: `PORT=5055 make start`
- Quick verify: `PORT=5055 bash tools/verify_suite.sh` (report → logs/verify_*.md)
- Static checks: `make check`
- Lock flow: `curl -sS 'http://127.0.0.1:5055/api/command?cmd=/radar%20lock%20nearest'`
- Radar sanity: `curl -sS 'http://127.0.0.1:5055/radar/force_spawn_near?class=Aircraft&range=2.5'`

## Next Small Steps (pick one)
1) Extract CAP routes to a blueprint (routes/cap.py), register in webdash.
2) Remove /__old/weapons/* placeholders after confirming UI uses /weapons/* only.
3) Case hygiene: normalize imports to projects.FalklandV2 (avoid case‑sensitivity pitfalls).
4) Continue slimming webdash.py by moving any remaining route clusters.

## Savepoints
- Use rescue tags before changes: `git tag rescue-$(date +%Y%m%d_%H%M%S)` then `git push --tags`.
- Recent tags: `rescue-main-*`, `rescue-rollback-*` (already pushed).

## Notes
- make check warns if files get too large. webdash.py is still big by design; we’re reducing it in small slices.
- Optional: enable pre-push guard locally: `ln -s ../../tools/hooks/pre-push.sample .git/hooks/pre-push`.
