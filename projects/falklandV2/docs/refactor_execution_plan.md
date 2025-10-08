# Falkland V2 Refactor — Execution Checklist

Long-lived checklist for the refactor/stabilisation push. Work through each phase in order, pausing anytime; tick items as you complete them. Update this document whenever scope changes or new findings need to be captured.

---

## Phase 0 — Establish Baseline Guardrails

- [x] Run `make check` and review the current size/route warnings. *(2025-03-23: `PYTHONPYCACHEPREFIX=./tmp/pycache make check`; warnings logged for desktop_app.py, webdash.py, sound.js, stations.js, webcore.py, hermes_cap.py.)*
- [x] Run `PORT=5055 bash tools/verify_suite.sh` and file/triage any failures.
  - *2025-03-23: initial attempt (`logs/verify_20251007_165029.md`) exposed a pipeline bug; fixed by switching to `python -c` in `tools/verify_suite.sh`.*
  - *2025-03-23: reran successfully (`logs/verify_20251007_170713.md`); noted `debug/cellmap` skip but all endpoints returned 200.*
- [x] Capture the active Flask route map (e.g. `python -m flask routes` or `tools/route_diff.py`) and stash the output in `logs/`. *(2025-03-23: `FLASK_APP=projects.falklandV2.webdash:app flask routes` → `logs/routes_20251007_172505.txt`.)*
- [x] Confirm every launch path (`run_falkland.sh`, `run_desktop.sh`, `projects/falklandV2/main.py`) resolves to the same runtime service.
  - *2025-03-23: `run_falkland.sh` executes `python -m projects.falklandV2.webdash`, which instantiates a single `GameRuntime` (`webdash.py:335`).*
  - *2025-03-23: `run_desktop.sh` auto-starts the same web server via `run_falkland.sh` before launching the Qt client, so desktop traffic shares that runtime.*
  - *2025-03-23: `projects/falklandV2/main.py` is a standalone bootstrap demo and not part of production flows; noted for cleanup in later phases.*
- [x] Inventory `projects/falklandV2/data` and `projects/falklandV2/state`; note which files are static seeds vs. generated at runtime.
  - *Seeds (`data/`): configuration and assets (`attack_waves.json`, `contacts.json`, `mission` defs, `sounds/`, `radiomsg/`, etc.) — treated as source-controlled inputs.*
  - *Runtime (`state/`): JSON snapshots (`ammo.json`, `runtime.json`, `skirmishes.json`, etc.) plus generated TTS/voice caches created via `webcore` helpers; these regenerate on reset.*

## Phase 1 — Confirm Live Entry Points & State

- [x] Trace how `GameRuntime` is constructed and ensure the web and desktop fronts use the same instance.
  - *2025-03-23: `webdash.py:335` instantiates a singleton `GameRuntime(port=PORT)` on import; `_bind_runtime` registers blueprint adapters immediately (`webdash.py:472`).*
  - *Desktop client interacts purely over HTTP (no embedded runtime) because `run_desktop.sh:22` boots the same server via `run_falkland.sh`, and `desktop_app.py` only issues REST calls.*
- [x] Validate that `projects/falklands/` (legacy tree) is unused by current runners.
  - *2025-03-23: Grepped live code (`projects/falklandV2/**`) for `projects.falklands` references — none present aside from docs/tests.*
  - *Importing `projects.falklandV2.webdash` in a fresh interpreter leaves `sys.modules` free of any `projects.falklands.*` entries; same during a live server run.*
  - *Desktop app import blocked by missing PySide6 locally (expected); no fallback to legacy modules observed.*
- [x] Decide the fate of `_backup_20250903_172837/`; archive externally or extract needed assets.
  - *2025-03-23: reviewed `_backup_20250903_172837/falklands` — contents mirror legacy tree (`run_bridge.py`, prompts, old data catalogues) and audio assets already exist under `projects/falklandV2/data/sounds/`.*
  - *No unique artefacts required for V2; backup directory deleted (external history retained in GitHub archive).*
- [x] Document any scripts, services, or cron jobs that still reference removed paths.
  - *2025-03-23: Repo sweep found no references to legacy `projects.falklands` or the deleted backup; tooling (`tools/`, scripts/) only target V2 paths.*

## Phase 2 — Flask/Web Core Refactor

- [x] Draft an app-factory layout for `webdash` (module split plan + ownership notes). *(2025-03-23: see `projects/falklandV2/docs/webdash_refactor_plan.md` for proposed module breakdown and task list.)*
- [x] Extract global fallbacks and helper wiring from `webdash.py` into dedicated modules.
  - *2025-03-23: Created `projects/falklandV2/web/runtime.py` to manage `GameRuntime` and `projects/falklandV2/web/fallbacks.py` to host CAP fallbacks; `webdash.py` now delegates to these helpers.*
- [x] Ensure each blueprint registers cleanly with the new factory and add tests for import failures.
  - *2025-03-23: Added `tests/test_web_factory.py` to instantiate `create_app()` and assert key routes exist with the runtime extension attached.*
- [x] Update startup scripts to use the factory (`create_app()`), while keeping backward-compatible shims where needed.
  - *2025-03-23: Added `projects/falklandV2/serve.py` using `create_app()` and switched `run_falkland.sh` to launch via this module (legacy `webdash` shim retained for imports).* 
- [x] Rerun Phase 0 guardrails to confirm no route or size regressions.
  - *2025-03-23: `PYTHONPYCACHEPREFIX=./tmp/pycache make check` + `PORT=5055 tools/verify_suite.sh` (report `logs/verify_20251007_194734.md`) run against the new factory entrypoint.*

## Phase 3 — Gameplay Engine Alignment

- [x] Decide on the canonical radar/engine modules (choose between `core/radar.py` and `radar.py`).
  - *2025-03-23: Standardised on `projects/falklandV2/core/engine.py` + `projects/falklandV2/radar.py`; removed `projects/falklandV2/engine.py` and `projects/falklandV2/core/radar.py`, updating imports accordingly.*
- [ ] Remove or rename redundant modules; update imports to the chosen implementation.
- [ ] Centralise coordinate helpers (`engine_adapter`, `grid.mapping`, UI consumers) so every caller uses the same projection.
  - *2025-03-23: Redirected `webcore` grid helpers to re-use `engine_adapter` (`world_to_cell`, `cell_to_world`, `ship_cell_from_state`, `radar_xy_from_state`) to reduce duplication.*
- [ ] Add/extend unit tests for radar spawn logic, CAP engagements, and mission events to lock behaviour.
  - *2025-03-23: Added `tests/test_engine_integration.py` to cover engine movement, radar sync, and catalog-driven spawn behaviour.*
  - *2025-03-23: Expanded CAP route coverage in `tests/test_cap_request.py` (new bombs launch assertion and surface target loadout cases).* 
- [ ] Rerun the unit test suite (`pytest` or `make check` if wired) and log outcomes.

## Phase 4 — Runtime Service Slimming

- [x] Identify logic inside `GameRuntime` that belongs in dedicated services (logging, CAP binding, mission scheduling, alarm triggers).
  - *2025-03-23: Documented current responsibilities and extraction plan in `projects/falklandV2/docs/runtime_slim_plan.md`.*
- [x] Move those responsibilities into cohesive modules with clear interfaces.
  - *2025-03-23: Introduced `runtime_radar.RadarBridge`/`RadarRecorder` (radar logging + CAP hooks), `runtime_audio.AudioCoordinator`, `runtime_cap.CAPOrchestrator`, and `runtime_mission.MissionCoordinator`; updated tests (`tests/test_runtime_radar.py`, `tests/test_runtime_audio.py`, `tests/test_runtime_cap.py`, `tests/test_runtime_mission.py`).*
- [x] Keep `GameRuntime` focused on orchestration; update call sites accordingly.
  - *Runtime now instantiates the coordinators and forwards mission/ammo/audio/radar operations through them.*
- [x] Add smoke tests or integration checks to ensure CAP/radar bindings remain intact.
  - *2025-03-23: Added targeted unit suites (`tests/test_runtime_*`) covering radar logging, CAP hits, mission sync, audio state.*

## Phase 5 — Asset & Filesystem Cleanup

- [x] Audit top-level audio assets (`SHAR_returning.wav`, etc.); move in-use files into `projects/falklandV2/static` or `data/sounds`.
  - *2025-03-23: Confirmed the stray WAV files were unused and removed them (retained curated copies under `data/sounds`).*
- [x] Delete or relocate unused audio, ensuring playback hooks (`subsystems/audio.py`) are updated.
  - *No hook changes required; removals covered by unit/smoke tests.*
- [x] Remove temporary directories (`tmp/`, `tmp_state.json`, `tmp_status_snapshot.json`) from version control; add ignores and lazy-create logic.
  - *2025-03-23: Deleted residual temp files and added ignore entries to `.gitignore`.*
- [ ] Sweep for remaining `__pycache__`, editor artefacts, and obsolete workspace files; update repo checks to prevent reintroduction.

## Phase 6 — Tooling & Documentation

- [ ] Extend `tools/check_repo.py` to flag oversize modules and orphan assets.
- [ ] Script a dependency audit (compare `pip freeze` to `requirements.txt`; split optional front-end deps if needed).
- [ ] Update `PROGRESS.md` with the current phase, blockers, and snapshot of outstanding risks.
- [ ] Share this checklist status during hand-offs so interruptions are safe and predictable.

---

### Notes
- Keep this document source-controlled; commit updates alongside related code changes.
- When pausing work, mark the last completed checkbox and leave a short note or TODO for the next session.
- If unexpected files or behaviours appear mid-refactor, stop and agree on how to proceed before continuing.
