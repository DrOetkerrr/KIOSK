# GameRuntime Slimming Plan

Draft notes for Phase 4: reduce `projects/falklandV2/runtime_service.py` to orchestration duties and push feature logic into dedicated modules.

---

## Current Responsibilities (February 2025 snapshot)

`GameRuntime` currently:
- Creates/owns the gameplay services (Engine, Radar, HermesCAP, MissionController, wave schedule).
- Exposes Flask-facing hooks (`record_flight`, `record_radio`, audio triggers, CAP stamp helpers) by forwarding to `subsystems.webcore`.
- Manages runtime state persistence paths (ammo, arming, crew, roadmap, etc.).
- Maintains mission settings cache, applies CAP/radar filters, syncs wave schedule.
- Hosts CAP hit callbacks that reach back into `webdash` for ship damage resolution.
- Handles radar alarm auto-trigger logging via `_RadarRecorder`.
- Tracks last tick timestamps, applies mission contact filters, and binds CAP mission providers.

Pain points:
- Hard to test in isolation; heavy reliance on `webdash` globals and `webcore` singletons.
- Audio, mission, and CAP logic bleeds across modules, making it difficult to reason about responsibilities.
- Many methods (`_apply_mission_contact_filters`, `_sync_cap_wave`, CAP hit hook) deserve their own services.

## Desired Architecture

Split `GameRuntime` into smaller collaborators:

1. **RuntimeShell (`runtime_service.py`)**
   - Minimal orchestrator that wires Engine, Radar, CAP, MissionController, and delegates lifecycle signals (start, reset, shutdown).
   - Exposes a narrow API (get_engine, get_cap, get_radar, tick, reset_state).
   - Owns a service registry or `SimpleNamespace` to hand to Flask (`runtime_mgr.attach_runtime`).

2. **StateRepository (new module, e.g. `runtime_state.py`)**
   - Encapsulates paths and lazy JSON loading/saving for ammo, arming, crew, roadmap, etc.
   - Provides typed accessors and reset helpers so tick logic can request state without touching filesystem paths directly.

3. **CAPOrchestrator (new module under `subsystems` or `runtime/`)**
   - Manages CAP integrations: mission snapshots, loadout bookkeeping, hit resolution, vector/launch linking.
   - Offers callbacks for Radar/Engine to consume without pulling in webdash globals.

4. **RadarBridge**
   - Wraps `_RadarRecorder` alarm logic and CAP mission providers.
   - Accepts dependencies (record_flight, trigger_alarm, cap snapshot provider) via constructor.
   - Keeps runtime focused on instantiation rather than logging side effects.

5. **AudioCoordinator**
   - Holds audio state, intro payload, and alarm helpers currently forwarded from `webcore`.
   - Could live in `subsystems/audio_runtime.py`; runtime would compose it instead of touching `core.AUDIO_STATE` directly.
   - ✅ 2025-03-23: Added `projects/falklandV2/runtime_audio.py` (`AudioCoordinator`) and updated runtime/tests to use it.

## Incremental Steps

1. **Inventory dependencies**
   - Document every attribute grabbed from `webcore` (record_flight, trigger_alarm, etc.) and decide which service should own it.
   - Note where `runtime_service` reaches into `webdash` (CAP hit callback) and replace with dependency injection.

2. **Introduce StateRepository**
   - Move path attributes and simple load/save helpers into a new module.
   - Update `GameRuntime` to hold a `StateRepository` instance instead of dozen path attributes.
   - ✅ 2025-03-23: Implemented in `projects/falklandV2/runtime_state.py`; runtime composes it and tests cover path/JSON helpers.

3. **Extract RadarBridge**
   - Lift `_RadarRecorder` and CAP mission provider wiring into a cohesive class (e.g., `RadarCoordinator`).
   - Provide clean hooks for CAP to register effects without reaching into runtime internals.
   - ✅ 2025-03-23: `projects/falklandV2/runtime_radar.py` (`RadarBridge` + `RadarRecorder`) now handles logging/CAP wiring; covered by `tests/test_runtime_radar.py`.

4. **CAPOrchestrator**
   - Move CAP hit handling, CAP mission sync (`_sync_cap_wave`), and CAP meta caches into a dedicated object.
   - Inject dependencies (`apply_enemy_ship_damage`, voice emitters) from the caller.
   - ✅ 2025-03-23: Implemented `runtime_cap.CAPOrchestrator`; runtime delegates wave sync + hit routing, with tests in `tests/test_runtime_cap.py`.

5. **Slim GameRuntime**
   - Once the above collaborators exist, strip `GameRuntime` down to:
     - Service creation and wiring (`Engine`, `Radar`, `CAP`, `MissionController`).
     - Public API for Flask/desktop entrypoints (ticks, reset, snapshot).

6. **Testing**
   - Add unit tests for each new module (StateRepository path logic, RadarBridge alarm behaviour, CAP orchestrator vector/launch flows).
   - Expand `tests/test_engine_integration.py`/`tests/test_cap_request.py` or create new tests that instantiate the runtime with fakes.

## Open Questions

- How much of `webcore` should move alongside the runtime split? (e.g., audio helpers, CAP meta caches).
- Should mission wave syncing live inside `MissionController` instead of runtime?
- Can we decouple the CAP hit callback from `webdash` entirely by introducing a ship damage service?

Document updated: 2025-03-23  
Next author: continue with Step 2 (StateRepository extraction).
