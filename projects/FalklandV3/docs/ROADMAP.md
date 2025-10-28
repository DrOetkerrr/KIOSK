# Falkland V3 Build Roadmap

## Phase 0 — Foundations
- [x] Capture V2 gameplay inventory.
- [x] Draft architecture and module layout.
- [x] Stand up runtime core (engine, event bus, snapshot reducer).
- [x] Land FastAPI app with `/api/status` parity scaffold.
- [x] Wire CI (lint, pytest) for new workspace.

## Phase 1 — Simulation Core
- Implement radar contact generator + wave loader.
- Port CAP sortie logic onto event bus.
- Rebuild mission decision trees + audio script queue.
- Add deterministic tick scheduler and time abstraction.

### Completed in this iteration
- CAP sorties now remove hostile contacts and emit radio/audio intercepts.
- Mission manager supports timer/hostile kill conditions with JSON-driven configs.
- Health manager tracks Hermes and allies; mission failure triggers announcer/decision prompts.
- Added mission decision API/UI hooks so bridge choices resolve abandon-ship prompts.
- Introduced Sea Harrier sorties with fuel state, status telemetry, and CAP integration.

## Phase 2 — API Surfaces
- Expose station-specific projections (`/stations/nav`, `/stations/radar`, etc.).
- Implement command handlers (course, speed, arming, radio).
- Generate JSON schemas & fixtures for parity tests.
- Integrate structured telemetry + watchdog.

## Phase 3 — Stations Frontend
- Bootstrap Vite + TypeScript workspace with shared client store.
- Implement NAV/RADAR/WPN/RADIO/ENG panels with modular components.
- Add WebSocket diff subscription fallback to long polling.
- Port audio playback + connection status banner.

## Phase 4 — Parity & Migration
- Replay V2 scenarios against V3, assert contact/mission outcomes.
- Build cutover tooling and feature flags for per-station rollout.
- Document deployment + recovery runbooks.

## Phase 5 — Live Ops & Observability
- Instrument runtime metrics (ticks, mission state, contact counts) into Grafana dashboards.
- Build admin/operator console for mission overrides, live SAR tasking, and CAP resupply.
- Implement runtime-configurable toggles for scenario packs, audio barks, and announcer cadence.
- Integrate automated alerting hooks for event bus stalls, telemetry gaps, and API error spikes.

## Phase 6 — Experience Polish
- Expand Sea Harrier and Hermes storylines with branching briefings and debrief VO.
- Layer in progressive difficulty scaling and optional tutorial overlays per station.
- Tighten audio mix, annunciator priorities, and low-bandwidth fallbacks for streaming assets.
- Ship accessibility pass (color palettes, captioning, hotkey remaps) across all bridge panels.

## Phase 7 — Post-Launch Expansion
- Add North Atlantic weather systems with dynamic sea states and radar attenuation modeling.
- Introduce cooperative multiplayer sorties with shared sensor picture and role handoff.
- Deliver scenario editor toolkit + docs for community mission authoring.
- Explore console kiosk hardware integration, packaging, and remote update pipeline.

## Rebuild Workstreams
- **Simulation Core Parity:** Flesh out radar waveforms, contact behaviors, mission schedulers, and failure handling to mirror V2 while unlocking deterministic replay.
- **Systems & Telemetry:** Harden event bus serialization, telemetry collectors, and watchdog processes so every station receives synchronized state with backpressure controls.
- **Stations UX Rebuild:** Recompose station panels with shared design system, adaptive layouts, offline fallbacks, and audio/control affordances that match legacy muscle memory.
- **Tooling & Ops:** Automate schema generation, mission fixture refresh, deployment orchestration, and operator consoles to reduce manual rebuild toil.

## Rebuild Readiness Gates
- Parity acceptance harness passes for CAP missions, Sea Harrier sorties, and scripted bridge prompts.
- CI pipelines gate on fixture drift, schema validation, and deterministic replay diffs.
- Observability dashboards confirm mission loop health across soak runs (>24h) with no stalled ticks or telemetry gaps.
- Launch runbook signed off by ops covering rollback, data snapshots, and kiosk update flows.

## Active Sprint — Rebuild Milestones
- **Simulation Core:** Finalize radar wave loader integration; backfill hostile contact behaviors for Exocet runs; wire CAP kill confirmations into mission decision resolver.
- **Systems & Telemetry:** Stabilize tick scheduler drift under accelerated time; land telemetry batching to eliminate dropped frames; ship watchdog alarms into staging Grafana.
- **Stations UX:** Port NAV/RADAR layouts to the new design system; hook audio annunciators into shared bus; validate offline start-up path for kiosk deployments.
- **Tooling & Ops:** Automate mission fixture regeneration via CI job; publish schema diffs nightly; draft operator console playbook for CAP overrides.

## Open Risks & Mitigations
- **Radar Fidelity:** Waveform modeling still diverges from V2 during heavy clutter; mitigation is to introduce golden trace replay tests and borrow V2 coefficients before tuning.
- **Telemetry Backpressure:** Event bus can queue >5s during mission spikes; mitigation is priority channels for annunciators and staged rollout of batching changes.
- **Frontend Perf:** Stations bundle currently over budget; mitigation is component-level code splitting and deferring heavy charts until interaction.
- **Ops Staffing:** Only one operator trained on new console; mitigation is to schedule handoff sessions and record runbook-driven dry runs.

## In-Flight Progress Notes
- Radar wave loader now ingesting three baseline scenarios; clutter modeling patch pending review from simulation team.
- Tick scheduler drift reproduced and traced to wall-clock jitter; instrumentation patch merged, awaiting stability soak.
- NAV panel migrated to design system components; RADAR panel mid-port with contact timeline still stubbed.
- Mission fixtures auto-regenerated nightly; schema diff job blocked on CI runner permissions, ops is provisioning access.
- NAV station API projection (`/api/stations/nav`) live with history window support for frontend hookup.
- RADAR station projection (`/api/stations/radar`) exposes prioritized contact feeds and wave telemetry for UI wiring.
- Weapons station projection (`/api/stations/weapons`) now surfaces slot states + armed counts for the new console.
- Radio station feed (`/api/stations/radio`) provides capped message backlog and category summaries for announcer UI.
- Engineering station projection (`/api/stations/engineering`) surfaces asset health + weather telemetry with critical alerts.
- CAP intercepts now respect contact weapon profiles (Exocet, long-range strikes) and broadcast threat-specific radio/audio cues.
- Weapons inventory now tracks ammo budgets, engagement envelopes, and exposes telemetry through `/api/status` and station feeds.

## Immediate Next Actions
- Stress-test Exocet regression scenarios to validate hostile behaviour tuning and weapon state handling.
- Tune telemetry batching thresholds in staging to keep announcer channel <250ms latency.
- Wire audio annunciators through shared bus and validate cross-station muting controls.
- Finish operator console playbook draft and schedule first dry run with ops.

## Cross-Team Dependencies
- Simulation: provide validated V2 radar coefficient export for clutter parity testing.
- Frontend: deliver charting package evaluation to unblock RADAR timeline component.
- Ops: finalize Grafana datasource credentials for watchdog dashboards.
- Audio: confirm announcer VO asset delivery dates for experience polish alignment.
