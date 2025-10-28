# Falkland V3 Architecture Overview

## Design Principles
- **Deterministic simulation core**: single authoritative game loop encapsulating ship, radar contacts, CAP, mission logic, and audio triggers.
- **Explicit domain boundaries**: navigation, radar, weapons, comms, mission, and audio each live in their own module with stable interfaces.
- **Event-driven orchestration**: subsystems publish/consume well-typed domain events instead of mutating shared globals.
- **Stateless delivery surfaces**: HTTP/WebSocket APIs project read-only views while commands mutate state through the core.
- **Test-first**: contract fixtures + property-based checks guarantee V3 reproduces V2 gameplay.
- **Incremental deliverability**: each station can attach independently, enabling partial play even before full parity.

## Module Layout
```
projects/FalklandV3/
  pyproject.toml                # Poetry/uv project metadata
  falklandv3/
    __init__.py
    core/
      clock.py                  # Tick scheduler + time abstraction
      engine.py                 # Ship kinematics + world grid
      radar.py                  # Radar simulation (spawns, tracks, filters)
      cap.py                    # CAP sorties, asset lifecycle
      mission.py                # Mission trees, decisions, outcomes
      audio.py                  # Audio script queue + playback state
      state.py                  # Aggregate state snapshot + reducers
      events.py                 # Event bus contracts + domain events
    services/
      runtime.py                # GameRuntime orchestrator (start/stop/tick)
      persistence.py            # State repository, recordings, asset loading
      telemetry.py              # Structured logging, metrics exporters
      watchdog.py               # Poll watchdog, lag detectors
    adapters/
      api/
        __init__.py
        server.py               # FastAPI app factory
        dependencies.py         # DI wiring for runtime + repositories
        routers/
          status.py             # `/api/status`
          nav.py                # Navigation commands
          radar.py              # Radar controls/debug
          cap.py                # CAP operations
          mission.py            # Mission selection + decisions
          audio.py              # Audio debug/ack
          diagnostics.py        # Health, logs
      cli/
        simulate.py             # CLI harness for local runs
      files/
        data_loader.py          # JSON/YAML load helpers
    stations/
      __init__.py               # Station projection helpers (NAV, RADAR…)
      nav.py                    # NAV station-specific view model
      radar.py                  # Radar console view model
      weapons.py
      radio.py
      engineering.py
  frontend/
    stations/
      package.json              # Vite + TypeScript setup
      src/
        main.tsx
        api/                    # Typed API client
        stores/                 # Event store + polling
        stations/               # NAV, RADAR, WPN, RADIO, ENG modules
        components/
      public/
  tests/
    unit/                       # Pure unit tests for each domain module
    integration/                # Runtime + API contract tests
    acceptance/                 # Golden fixtures mirroring V2 end-to-end
  docs/
    changelog.md
    gameplay_parity.md
    runbook.md
```

## Runtime Flow
1. `GameRuntime` boots via `services.runtime.GameRuntime`, loading configuration/assets from `persistence` and seeding the simulation state.
2. A deterministic `clock` drives `engine.tick(dt)` on a background thread (or asyncio loop); the engine raises typed events (e.g., `ShipMoved`, `ContactDetected`).
3. CAP, mission, and audio modules subscribe to the event bus:
   - CAP updates sortie status and emits `CapLaunchRequested`, `CapHit`.
   - Mission consumes radar/CAP events to evaluate win/loss trees and emits decision prompts or outcomes.
   - Audio consumes mission/CAP/alert events, updates playback queue, and produces voice/alarm directives.
4. `state.StateReducer` maintains a canonical snapshot updated by events, enabling consistent `/api/status` projections without re-computing derivations each poll.
5. API routers expose **commands** (POST/PUT) that dispatch intent events (e.g., `SetCourse`, `ArmWeapon`, `AcknowledgeDecision`) and **queries** (GET) that return read-only view models.
6. Stations front-end maintains a shared client store, polls or subscribes via WebSocket for state diffs, and renders per-station panels from the typed schemas.

## Data Contracts
- Define Pydantic models in `api.schemas.*` for every payload (status snapshot, station-specific views, command DTOs).
- Generate JSON Schemas during build and store under `docs/contracts/`.
- Golden fixtures recorded from V2 power snapshot-based regression tests ensure parity (see `docs/falklandV3/gameplay_parity.md`).

## Persistence & Assets
- Asset loader keeps compatibility with existing JSON asset packs (`data/`, `state/`) while allowing overrides per environment.
- Flight recorder, crew roster, weapon arming, and alarms persist via repository abstractions; they can be swapped for database implementations later.
- Audio playback delegates to platform-specific adapters (e.g., `subsystems/audio_backend.py`) to keep core logic pure.

## Stations Strategy
- Each station module defines a projection (`StationState` model + reducer) derived from the canonical snapshot.
- UI consumes the projection via `/api/stations/<station>` endpoints or WebSocket channels, reducing cross-station coupling.
- Commands surface at the station level (e.g., NAV `POST /stations/nav/course`), internally translating to domain events.

## Testing Strategy
- Unit: deterministic ticks, CAP wave transitions, mission condition evaluations, audio queue state.
- Integration: `/api/status` parity against recorded V2 fixtures; command→event→state assertions.
- End-to-end: headless stations client that runs mission scenarios mirroring V2 smoke tests.
- Continuous verification via GitHub Actions workflow (pytest + frontend typecheck/build).

## Roadmap Hooks
- Phase-based implementation with milestones (runtime core, APIs, stations UI, parity validation, observability).
- Feature flags allow toggling between legacy (V2) and new (V3) endpoints during rollout.
- Observability via structured logging + OpenTelemetry exporters for tick duration, poll latency, CAP events.

This architecture gives Falkland V3 isolated domain surfaces, deterministic simulation, and per-station lifecycles while preserving every gameplay behaviour captured in the baseline inventory.
