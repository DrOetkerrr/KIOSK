Updated reference - Falkland V3 is now synced to the live build that shipped the desktop shell, the resupply loop, and the revised CAP engine. The document still has the dual structure: Part I is the strict contract the code must obey; Part III is the plain-language UI brief; Part IV carries binding tables and quick examples.

Falkland V3 - Dual Reference (Strict Spec + Full Design)

Part I - Strict Spec (Codex Reference)

Core world model

The simulation runs on a 40x40 nm world. The bridge threat board is a centred 26x26 grid (columns A-Z, rows 1-26) at 1 nm per cell. Own ship boots in K13 on heading 000 deg and 0 kts unless a scenario overrides it. Hermes and Coventry ride in the convoy model: they are always tracked, surface in radar/status payloads as `fleet:` entries, and never consume hostile slots. Engine ticks are 1 s; the runtime throttles between 0.05 s and 1.0 s to keep radar, CAP, and audio smooth.

Course orders slew at 1 deg/s toward the commanded heading; speed clamps to 32 kts (ship.json). Movement uses ktsxdt/3600. Convoy escorts honour a 30-50 s lag before adopting the leader’s new course/speed; Hermes offset commands (`/nav hermes close_in|stand_off`) adjust formation spacing by 1 nm steps while preserving that lag. Lives are tracked per hull: Sheffield 4, Hermes 8, Belgrano 8. UI health percentages are derived from those pools (Sheffield: 100/75/50/25/0; Hermes: 100 down in 12.5 % steps). When Sheffield hits 0 lives the abandon-ship routine triggers; Hermes at 0 emits `eng.hermes.outofaction` and halts CAP launches.

Stations and roles

RDR owns radar control, threat prioritisation, and the alert ladder. WPN/FCR governs arming, range gating, launch, cooldowns, and shot bookkeeping. NAV handles course/speed, convoy offsets, and mission selection. COMMS controls CAP, Hermes liaison, resupply, and mission prompts. ENG manages repair teams, system timers, and health state.

Radar (RDR)

Automatic scans fire every 180 s; manual `/radar scan` flushes immediately. Spawns are time-based: normal traffic uses a Poisson rate of 0.12/min, surprise events 0.02/min. Surprise inserts a single hostile at 10-14 nm; otherwise contacts spawn 15-40 nm out. Non-surprise rolls have a 30 % chance to spawn a friendly (usually escorts or civilian traffic); surprise is always hostile. All contact picks come from `data/contacts.json`; hostiles respect weighted odds and CAP modifiers.

The radar can carry 20 live contacts. CAP flights injected by Hermes and Sea King resupply flights do not count toward the hostile cap. Surface ships spawn no closer than 20 nm; Super Étendards never enter on the 10 nm surprise rule and are forced to >=20 nm. All other hostile aircraft arrive in two-ship elements: a leader plus a wingman that trails in a 2-cell (≈2 nm) line-abreast/tail formation. Formation logic clamps to the world bounds; if the aft slot would clip the edge it shifts the wingman laterally while keeping at least 75 % of the requested spacing, and once the element closes to 5 nm or less the runtime reasserts a full two-cell gap to keep them clear of debris from the leader. Hostiles steer gently toward own ship unless flagged `retreating`.

Priority selection honours manual locks. Without a lock it picks the closest hostile, breaking ties by spawn weight then detection time. Priority alarms log `ship.alarm.threat_close` whenever the current hostile closes to <=3 nm, with a 30 s cooldown per target. Hermes CAP effects feed back into radar: active stations apply `spawn_weight_multiplier` and `intercept_prob_pre_release`, so some aircraft are intercepted before they appear. CAP missions also inject friendly contacts at their current cells and move as missions transition between queued/airborne/on-station/RTB. Resupply launches inject a friendly Sea King contact while the helicopter is en route or landing. When a hostile aircraft comes within 10 nm of any on-station CAP flight the deterrence rule fires: 30 % of those aircraft flip to `retreating`, roll their own heading away, and drop to low threat.

Weapons and fire control (WPN/FCR)

Arming is stateful: pressing ARM schedules a 5 s delay, records the pending state on disk, then flips to Armed and starts cooldown tracking. SAFING clears the flag immediately. All fire modes (real or test) require the weapon to be in the Armed state and off cooldown. Cooldowns are class-driven: Missiles 8 s, SAMs 6 s, Decoys 5 s, Guns 2 s unless `weapons_catalog.json` provides an override.

`/weapons/fire` enforces primary-lock and range gates. Primary data comes from the radar priority; `compute_in_range` uses the catalog’s `min_nm`/`max_nm` and supported target classes to decide whether the shot is legal. Test fire consumes ammo (1 round for missiles/exocet/gun salvo, 50 rounds for the automatic guns) but skips targeting rules; range gating still colours the UI. Real fire subtracts ammo, schedules a shot event, applies cooldown, and logs to the event bus. Shot resolution uses class flight-time envelopes: Missiles & SAMs resolve after `3 s + range_nm / 1.94` (~Mach 3 behaviour), Guns at `2 s per nm`, everything else at `3 s per nm`. Per-shot Pk comes from the catalog via `_pk_from_range`. When a surface ship is hit the damage helper applies 1 HP for Sea Dart, 2 HP for bombs; hostile ships begin with 4 HP and switch to retreat behaviour once tracking shows they are fleeing or sunk. Range gating is mirrored in the UI (`IN RANGE` badge, timer column) and in the server’s invariant guard.

Sheffield gameplay loadout (intentional alt-history)

Sea Dart SAM - 26 missiles; 2-35 nm; supports Aircraft and Ship. 4.5 inch Mk.8 gun - HE 550, Illum 100; 1-8 nm; supports Ship/Shore. 20 mm Oerlikon - 5,000 rounds; 0.25-0.5 nm; aircraft focus. Twin 20 mm GAM-BO1 - 1,850 rounds; 0.3-2.5 nm; aircraft/small craft. MM38 Exocet - 4 missiles; 8-22 nm; surface only. Corvus chaff - 15 salvoes; 0-1 nm defensive bloom.

Communications, CAP, and logistics

Hermes CAP is driven by `data/cap_config.json`. Ready pairs max at 6, the airframe pool holds 10 aircraft (5 pairs). Launch cadence honours a 60 s minimum interval and a 60 s deck cycle; intercept missions use a 12 s deck cycle. Default on-station time is 10 min (CAP), intercepts hold for 2 min unless retasked. Station radius is 10 nm. Max CAP stations in play is 3, with a surge ceiling of 4 pairs; beyond that `/cap` responds “All CAP sorties committed.” Each pair carries four AIM‑9; bombs loadouts (4 weapons) are allowed for surface missions and auto-convert to intercept if `follow=hermes` is requested. Ready state reflects cooldowns (`cooldown_s`, `launch_interval_left_s`) so UI can disable buttons when the deck is cycling.

Re-vector logic: if a pair is airborne or on-station, has missiles left, and sits within 15 nm of the locked target, `/cap/intercept` reuses that mission and returns a TOT estimate. Otherwise a fresh intercept launches from Hermes. CAP missions flagged `follow=hermes` update their station cell every tick so the protection bubble rides with the flagship. While on-station the CAP mission requests permission once a hostile crosses 15 nm from the station centre; denials prompt every 30 s. If no permission arrives within 10 min the mission times out and RTBs. `auto_engage` is only active when permission is granted; Pk points are pulled from config (2 nm → 0.90, 3 nm → 0.80, 4 nm → 0.70, 5 nm → 0.60).

Harriers that go Winchester immediately RTB. Recovery + rearm is 5 min (`pair_rearm_refuel_min`). When a mission completes the pair returns to the ready pool, the airframe stock is replenished up to the configured max, and CAP history is rotated to keep at most 12 missions.

Resupply is handled via `/resupply`. Launching spins up a Sea King helicopter, records its origin cell (Hermes via convoy offsets), injects a friendly radar contact, and sets `stage='enroute'` with default ETA 180 s unless overridden. When ETA hits the runtime promotes the stage to `landing`, plays `Seaking.wav`, and emits `resupply.ready`. If the frontend fails to acknowledge within 15 s the fallback path calls `/resupply/complete`, clears the contact, emits `resupply.complete`, and refills ammo to the catalog defaults (preserving any higher-than-default counts). Cancel resets the state to idle without refilling.

Engineering and damage

Engineering tracks seven systems: Navigation, Radar, FireControl_Weapons, COMMS, Engine/Propulsion, Rudder/Steering, Hull (with fire/breach sub-status). Four repair teams exist. Enemy hits pick a random OK system, push it to `Offline`, stamp `response_deadline_ts = now + 120 s`, and subtract a Sheffield life or a Hermes life depending on the target. Assigning a team to an Offline or Damaged system starts/restarts a 120 s repair timer. The timer decrements while a team is present; hitting zero restores the system to OK, frees the team, and clears deadlines. If a system is Offline and no team answers before `response_deadline_ts` the state downgrades to `Damaged` (no additional life loss); assigning a team to a Damaged 0 s entry also sets the timer back to 120 s. Timers pause when teams are pulled off. Hull sub-status (`fire`, `flood`) is surfaced via badges and colour in the ENG UI and in `status` payloads.

Navigation and convoy

NAV keeps course/speed controls plus mission oversight. Course inputs accept numeric degrees or cardinals; speed is in knots. Submitting a new course shows a “rudder” countdown derived from the 1 deg/s slew rate. The board-edge predictor warns one tile early if the current course will exit the 26x26 threat board within the next hour. Convoy separation shows Hermes and Coventry ranges with an amber warning above 3 nm. Hermes offset commands expose close-in vs stand-off spacing; each command nudges offsets by one cell (1 nm) and the convoy module rotates offsets with ship heading to keep formation relative to the leader.

Missions, scenarios, and menu

`MissionController` loads `data/missions/end_conditions.json`. The default active mission is `protect_hermes`. Success/Failure branches are evaluated every tick using AND/OR logic against health, elapsed timers, and mission settings. Decisions are modelled via `DecisionState`: prompts carry configurable timeouts; once acknowledged or timed out they emit `mission.decision.*` events. Mission selection is exposed through `/mission/select` and the NAV UI. Scenario editing flows remain in the Menu station for debugging (set start cells, inject contacts, seed damage, etc.); mission cards display elapsed time, time remaining, and status badges (`Active`, `Success`, `Failure`, `Hold`).

Audio backbone

Ambient bridge audio stays below speech cues. Weapon launch sounds map through `_sound_key_for_weapon` so Sea Dart, Exocet, Mk.8, and chaff each carry distinct effects. `AUDIO_STATE` maintains shots-in-flight for HUD and for synchronising impact sounds with `_schedule_shot_result`. CAP voices use role-specific voices drawn from `data/voice_events.json`; permission prompts, FOX‑2 advisories, and RTB orders route through the Pilot voice. Launch callouts now fire when the mission actually transitions to `airborne`, so queued launches remain silent until the deck releases. Resupply launch/ready/complete cues tie into the same voice and audio tables.

Part III - Station UI Specs (Desktop App)

Radar Station (RDR)

The radar station shows a primary box plus a 10-row contact table. The primary box lists ID, name, range (hostiles at 0.01 nm precision), speed, and TTI (seconds until impact based on range/speed). Controls across the top include Scan, and hostiles/friendlies filters. The table columns are `#`, `Status` (allegiance badge), `Type` (class), `Name`, `Grid`, `Range nm`, `Speed kn`, `TTI s`, `ID`, and `Lock`. Rows are sorted by range and capped at ten for readability even though the backend tracks up to 20. `LOCK` buttons issue `/radar lock <id>`; the currently locked row is highlighted. Alerts (amber/red) mirror the backend close-threat rule. A carved-out area on the right honours destroyed contacts and recent events (five-line event strip). A separate “Shots in Flight” panel lives under Weapons, not Radar.

Weapons & Fire Control Station (WPN/FCR)

Top section: primary target summary identical to the radar primary box. Beneath it a Shots in Flight table shows every pending shot (weapon, target, grid, ETA, Pk %, result, range). If no shots exist the panel shows “No active shots.” A Test Mode toggle flips between real/test fire payloads.

The weapons table columns are `Weapon`, `Ammo`, `Range (nm)`, `Status` (IN/OUT OF RANGE with range tooltip when a primary exists), `Arm` button, `ARM Status` dot, `Timer`, and `Fire`. `Arm` toggles between SAFE→ARM (with arming progress) and SAFE when already armed. The timer column shows `ARM <seconds>`, cooldown seconds, or READY. The Fire button switches label to `TEST FIRE` when Test Mode is on and only enables when the weapon is armed, cooled down, has ammo, and the range check passes (or Test mode allows override). Each fire/arm action reports through the small message strip under the row (“ARMING…”, “FIRED”, error codes). The station keeps `LOCK`/`UNLOCK` controls, range badge, and PK cues consistent with backend state.

Navigation Station (NAV)

The NAV station combines mission control with helm inputs. Mission selector at the top lists available missions; switching calls `/mission/select` and shows status feedback. A mission card displays name, status badge, elapsed time, time remaining, sequence position, and brief success/failure rules. Below, course and speed inputs (numeric text boxes with GO buttons) send `/nav set heading=` and `/nav set speed=`. A status ribbon shows current cell, heading, speed, and convoy separation from Hermes/Coventry (with amber “Struggling to keep up” when >3 nm). Board-edge predictor displays warnings (“Approaching grid boundary: east (column Z)”) using the 1-hour look-ahead. Mission prompts (e.g., decisions) appear inline with confirm/hold buttons if the controller requests action.

Communications Station (COMMS)

Header shows Hermes cell, course, speed. CAP status panel lists ready pairs, committed pairs/airframes, cooldowns, Sidewinder inventory (pool + committed), and active tasks. Each active mission row displays flight number, loadout, current/target cell, target name, range, TOT/TOS counts, feasibility hints, permission state, missiles remaining, and buttons:
- `Engage` (authorise, toggles to `Hold` when already authorised)
- `Reassign` (retask to CAP grid or follow Hermes; disabled when payload or status forbids)
- `RTB`
Resupply controls sit under the CAP panel: `Launch Sea King` (disabled while active), status badge (EN ROUTE / LANDING / COMPLETE), and cancellation when available. Hermes follow retasks appear as part of the CAP rows (loadout forced to AIM-9). Permission prompts surface as toast dialogues when missions request ROE authorisation.

Engineering Station (ENG)

ENG shows flagship summary (Sheffield/Hermes health), CAP status snippet, and the repair table. The systems table columns are `#`, `Systems`, `Status`, `Timer`, `Repair`. Status badges use colour: OK (green), Damaged (amber), Offline (red), with icons for Hull fire/flood. Timer counts down while a team is assigned; once zero it flips to READY and the row auto-frees the team. The Assign/Release button toggles team state; when no teams are free the Assign side shows `NO TEAMS`. Rows with approaching response deadlines (>=50 % elapsed without a team) glow amber. Hull sub-badges (“FIRE”, “FLOOD”) appear next to the system name.

Menu Station (Scenarios and Debug)

Menu provides three pillars:
- Events Monitor: scrolling event feed with filters for station, severity, and system. Includes CAP, resupply, mission, and alarm events.
- Scenario Tools: load/save forms for start positions, contact injections, weather placeholders, ammo/damage seeds. Outputs scenario JSON to reuse later.
- Missions & Diagnostics: mission summaries with victory/defeat criteria, manual launch of the `protect_hermes` sequence, and hooks for diagnostics (`/diag/reset`, session start/end, debrief). The Sea King control mirrors COMMS so testers can exercise logistics without swapping stations.

Part IV - Worked Examples and Tables (for quick binding)

Flight times (seconds)
- Sea Dart (missile class) at 5 nm → ~5.6 s; 10 nm → ~8.2 s; 20 nm → ~13.3 s (`3 + range/1.94`).
- Exocet at 12 nm → ~9.2 s; at 22 nm → ~14.3 s.
- Mk.8 gun at 4 nm → 8 s; 8 nm → 16 s (2 s per nm).
- Chaff/other miscellaneous shots use 3 s per nm.

Range gates (from `weapons_catalog.json`)
- Sea Dart SAM: 2-35 nm; supports Aircraft, Ship.
- MM38 Exocet: 8-22 nm; supports Ship.
- 4.5 inch Mk.8: 1-8 nm; supports Ship, Shore.
- 20 mm Oerlikon: 0.25-0.5 nm; supports Aircraft, SmallCraft.
- 20 mm GAM-BO1 (twin): 0.3-2.5 nm; supports Aircraft, SmallCraft.
- Corvus chaff: 0-1 nm; supports Aircraft (defensive only).

Spawn rules and traits
- Normal spawn: 15-40 nm, Poisson λ=0.1667/min. Surprise: 10-14 nm, λ=0.0556/min.
- Friendly spawn probability on normal rolls: 0.3.
- Surface hostiles and Super Étendards are clamped to >=20 nm spawn ranges.
- CAP active → apply `spawn_weight_multiplier` (per target name) and pre-release intercept chance.
- Active CAP within 10 nm of a hostile → 30 % chance that hostile retreats.
- Manual locks override automatic priority until the contact vanishes or `/radar unlock` clears it.

Lives → health UI mapping
- Sheffield lives (4→0) map to 100, 75, 50, 25, 0 %.
- Hermes lives (8→0) map to 100, 87.5, 75, 62.5, 50, 37.5, 25, 12.5, 0 %.
- Belgrano (if scenario enables) maps 8→0 in the same steps as Hermes.

CAP readiness quick view
- Ready pairs: up to 6. Max concurrent CAP stations: 3 (surge 4).
- Airframe pool: 10 (pairs consume 2 frames; RTB recycles after 5 min).
- Launch gating: min 60 s between launches, deck cycle 60 s, scramble cooldown 60 s.
- On-station default: 10 min CAP, 2 min intercept. Permission timeout: 600 s.
- Sidewinder Pk: 2 nm 0.90, 3 nm 0.80, 4 nm 0.70, 5 nm 0.60.

TTI and alerts
- TTI = round(range_nm x 3600 / speed_kn). Only computed for hostiles with valid speed. Radar and WPN primary boxes display TTI in seconds.
- Alert ladder: amber at <=3 nm, red escalation allowed at <=1 nm. Cooldown per contact 30 s.

Repair timers
- Assigning a team to a Damaged/Offline system sets `timer_s = 120`. Timer decrements while assigned; hitting zero restores to OK and frees the team. Response deadlines (120 s from offline) mark rows amber if half elapsed without a team.

Resupply timeline (default launch)
- Launch (t=0): Sea King contact injected, ETA 180 s.
- Arrival (t~180 s): stage `landing`, `resupply.ready`, audio cue.
- Completion (frontend callback or fallback at +15 s): ammo refilled to defaults, Sea King removed, `resupply.complete` event broadcast.
