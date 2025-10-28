# Station Spec · NAV (Navigation)

## 1. Purpose & Critical Actions
- **Role**: Command the task group’s movement; provide at‑a‑glance awareness of the flagship and escorts.
- **Time‑critical actions**  
  1. Review current heading/speed/grid before issuing orders.  
  2. Enter new course.  
  3. Enter new speed (respecting ship limits).  
  4. Confirm order feedback or warnings.

## 2. Panels (no scrolling, 800 × 480)

### Panel A – `Overview & Orders`
- **Widgets**
  - Header badges: current grid (Sheffield, Hermes, Glamorgan), speed (knots), heading (deg).
  - Mini course tape (optional inline compass) for immediate bearing.
  - `Set Course` field + numeric keypad + `SET` button.
  - `Set Speed` field + numeric keypad + `SET` button (clamped to max speed).
  - Feedback strip for success/error messages.
- **Dependencies**
  - NAV history (last 3 orders) to show confirmation without leaving panel.
  - Current mission timer (read‑only) so helmsman sees urgency.
- **Interaction notes**
  - Buttons ≥48 px high, centred; keypad pops full width if we go virtual.
  - No modal dialogs; inline validation only.

### Panel B – `Fleet Snapshot`
- **Widgets**
  - Table of Sheffield, Hermes, Glamorgan: grid cell, speed, course, separation.
  - Optional “Hermes positioning” helper (close in / stand off toggles if retained).
- **Dependencies**
  - Read‑only Hermes/Glamorgan data from COMMS/ENG feeds.
- **Interaction notes**
  - Body scroll disabled; limit rows to three with fixed height.

### Panel C – `Nav History`
- **Widgets**
  - Chronological list (latest first) of nav orders with timestamp.
- **Dependencies**
  - Nothing else required; read-only.
- **Interaction notes**
  - Provide quick link back to Overview.

## 3. Layout Sketch (Panel A example)
```
┌───────── Header (60 px) ─────────┐
│ Sheffield AA17 | 21 kn | 045°    │
│ Hermes AB18   | 20 kn | 048°     │
│ Glamorgan AC16| 21 kn | 045°     │
├──── Course Block (150 px) ───────┤
│ [Compass strip]                  │
│ [Input field 000°][SET]          │
├──── Speed Block (150 px) ────────┤
│ [Input field 00.0 kn][SET]       │
├──── Feedback (40 px) ────────────┤
│ Success / error message          │
└──── History preview (80 px) ─────┘
```
No scrolling; remaining space reserved for padding (total ≤480 px).

## 4. Data & State
- **Fields**  
  - Course: integer degrees 0‑359; invalid input flagged in red.  
  - Speed: float knots, capped at Sheffield’s max (use tooltip to show limit).  
  - Grid cells: `AA00…BN39` (AA grid). Hermes/Glamorgan displayed for escort awareness.  
  - Mission clock: mm:ss, yellow < 5 min, red < 1 min.
- **Update cadence**  
  - Poll status every 2 s (shared with other stations).  
  - Form fields clear after successful submission.
- **State transitions**  
  - `SET` disabled while request in flight.  
  - Revert to current value when server rejects order (with inline message).

## 5. Alerts & Feedback
- Success: green banner “Course set to 045°”.  
- Error: red banner (e.g., “Speed exceeds 30 kn limit”).  
- Optional haptic ping (if hardware supports) on success/failure.  
- Auto-hide banners after 4 s; retain last order in history list.

## 6. Cross-Station Links
- Radar button (top-right) to jump directly to RADAR ➜ Contacts panel for threat verification.
- Log button opens LOG station filtered to NAV category.
- Hermes positioning hints should match COMMS station status (close-in/stand-off toggles stay sync’d).

---

# Station Spec · RADAR

## 1. Purpose & Critical Actions
- **Role**: Maintain situational awareness; identify, prioritise, and lock targets for the weapons team.
- **Time-critical actions**
  1. Review latest scan results / trigger manual scan (SCAN command).
  2. Identify nearest/most dangerous hostile contact.
  3. Lock desired target (one active lock at a time).
  4. Monitor friendly/friendly-fire risk (friendlies displayed separately).

## 2. Panels (no scrolling, 800 × 480)

### Panel A – `Friendlies`
- **Widgets**
  - Scan banner (timestamp of last sweep + `SCAN` button).
  - List of friendly contacts (sorted by range ascending, Sheffield excluded) with:
    - Grid cell, range (nm), relative bearing, speed (kn), heading (deg).
    - Time-to-reach Sheffield (based on closing speed; display `—` if opening).
    - Lock toggle (disabled for friendlies to prevent accidental lock).
  - Hermes status badge if present.
- **Dependencies**
  - Friendly contact feed from runtime radar snapshot.
- **Interaction notes**
  - Each row 64 px tall; lock button hidden/disabled for friendlies to avoid accidental engagement.

### Panel B – `Hostiles`
- **Widgets**
  - Mirrored layout to Panel A but for hostile contacts.
  - Row contents:
    - Primary label (e.g., “Bogey 17”), grid cell, range, bearing, speed, heading.
    - `TTI` (time-to-impact) relative to Sheffield (rounded seconds, red when ≤30 s).
    - Lock toggle button (`LOCK` / `UNLOCK`). Only one contact can be locked; selecting another unlocks previous automatically.
  - Locked contact row highlighted; header displays “Locked: <name>”.
- **Dependencies**
  - Locked contact ID shared with Weapons station.
- **Interaction notes**
  - Lock button height ≥48 px, positioned at row right edge. Provide audible click if hardware allows.

### Optional Panel C – `Scan History`
- **Widgets**
  - List of last 5 scans with hostile/friendly counts.
  - Manual `SCAN` button duplicated for convenience.
- **Interaction notes**
  - Only if space allows; otherwise fold into Panel A header.

## 3. Layout Sketch (Panel B)
```
┌──── Header (60 px) ──────────────────────────┐
│ Last scan 12:32:18  [SCAN]   Locked: Tiger-21 │
├──── Hostile list (360 px) ────────────────────┤
│ LABEL   Grid  Range  Bearing  Speed  TTI [LOCK]│
│ Tiger-21 BN18 12.4nm 045° 420kn 35s  [UNLOCK] │
│ Raider-07 BM22 18.1nm 030° 380kn 65s [LOCK]   │
│ …                                             │
├──── Alert strip (40 px) ──────────────────────┤
│ e.g., “New contact spotted at 020°/22 nm”     │
└───────────────────────────────────────────────┘
```

## 4. Data & State
- **Fields**
  - Grid cell: AA format.
  - Range: numeric in nm (one decimal).
  - Bearing: degrees (0–359).
  - Speed: knots; convert from payload.
  - TTI: use runtime-provided value if available, otherwise `distance / speed` fallback.
- **Update cadence**
  - Passive refresh every 2 s with snapshot.
  - `SCAN` triggers immediate backend refresh (display spinner/disabled state for 3 s).
- **State transitions**
  - `LOCK` button toggles, unlocking other rows automatically; reflect new lock within 0.5 s (or grey out until response).
  - Rows re-sorted after each update (closest on top).

## 5. Alerts & Feedback
- Scan success: green “Scan complete” toast (auto-dismiss 3 s).
- Scan failure: red banner “Scan unavailable; try again in 10 s”.
- Lock success: highlight row, show “Locked Tiger-21”.
- Lock failure: red inline message on row.

## 6. Cross-Station Links
- Primary `LOCK` affects Weapons station (panel uses same locked ID).
- Tapping a hostile name could open Weapons ➜ Shots panel (optional quick link).
- NAV integration: provide “Send course suggestion” quick entry (future enhancement).

---

# Station Spec · WPN (Weapons)

## 1. Purpose & Critical Actions
- **Role**: Execute defensive/offensive fires; monitor shot outcomes.
- **Time-critical actions**
  1. Confirm locked target details (range, bearing, closing speed, TTI).
  2. Arm / safe the appropriate weapon system.
  3. Fire (real/test) and verify launch succeeded.
  4. Track shots-in-flight for hit/miss confirmation and cooldown readiness.

## 2. Panels (no scrolling, 800 × 480)

### Panel A – `Target & Controls`
- **Widgets**
  - Locked target card (always top):
    - Label, grid, range, bearing, speed, TTI, mode (hostile/friendly).
  - Weapon quick-select (if multiple slots share same name) with status badge (Ready, Arming, Cooldown).
  - Action buttons: `ARM`, `SAFE`, `FIRE`, `TEST FIRE` (disable as needed).
  - Feedback strip for command results.
- **Dependencies**
  - Locked contact feed from RADAR.
  - Slot status from weapons snapshot.
- **Interaction notes**
  - Buttons stacked vertically, 64 px tall, full width.

### Panel B – `Weapon Inventory`
- **Widgets**
  - Categorized list of weapon slots, sorted in priority:
    1. SEA DART
    2. EXOCET
    3. MAIN GUN
    4. 20MM gun
    5. OERLIKON
    6. CHAFFF
  - Each entry shows: state badge, ammo/max, range envelope, cooldown remaining.
  - Tap to select slot (highlights in Panel A).
- **Dependencies**
  - Weapons snapshot from runtime.
- **Interaction notes**
  - No scrolling; use two-column layout if necessary (three slots per column).

### Panel C – `Shots In Flight`
- **Widgets**
  - Table listing active/resolved shots:
    - Weapon, target, grid, ETA, Pk %, result (pending/hit/miss/test/deployed), range.
  - Display last 4 entries; resolved shots linger ~6 s.
- **Dependencies**
  - Audio snapshot’s `shots_in_flight`.
- **Interaction notes**
  - Pending rows use amber; hit green; miss red; test blue; deployed yellow.

## 3. Layout Sketch (Panel A)
```
┌── Locked Target (150 px) ──────────────────────────┐
│ Tiger-21  BN18  12.4 nm  045°  420 kn  TTI 35 s     │
├── Weapon status (80 px) ────────────────────────────┤
│ SEA DART – Ready    Ammo 12/13    Cooldown 0 s      │
├── Controls (180 px) ────────────────────────────────┤
│ [ARM]                                                │
│ [SAFE]                                               │
│ [FIRE]                                               │
│ [TEST FIRE]                                          │
├── Feedback (50 px) ─────────────────────────────────┤
│ “Sea Dart fired (real)”                              │
└─────────────────────────────────────────────────────┘
```

## 4. Data & State
- **Fields**
  - Weapon state: Safe, Arming, Armed, Cooling.
  - Ammo: integer; highlight ≤25%.
  - Cooldown: seconds remaining.
  - Shot data: range, ETA (derived from runtime), Pk, result.
- **Update cadence**
  - Snapshot poll 2 s; actions optimistic update w/ rollback on failure.
- **State transitions**
  - Selecting a weapon updates active slot; other slots show passive state.
  - Fire/Test commands disable button until cooldown resets.
  - Shots move from pending → resolved; linger before removal.

## 5. Alerts & Feedback
- Command success: green banner (e.g., “Sea Dart armed”).
- Command failure: red banner with backend error message.
- Shots: pending rows amber, HIT green, MISS red, TEST blue, DEPLOYED yellow.
- Optional sound cue for “HIT” vs “MISS”.

## 6. Cross-Station Links
- Locked target card displays `View in RADAR` shortcut (opens RADAR ➜ Hostiles).
- Weapon selection remains highlighted when returning from other stations.
- Shots-in-flight panel surfaces same data as LOG (for event history consistency).

---

# Station Spec · COMMS (Flight Ops)

## 1. Purpose & Critical Actions
- **Role**: Manage SHAR (Sea Harrier) and Sea King air operations: mission planning, launch, retask, and recovery.
- **Time-critical actions**
  1. Build/modify missions for SHAR package (targets, loadout, timing).
  2. Launch SHAR or Sea King flights (quick access to ready aircraft).
  3. Monitor active flights, retask or order RTB, request resupply.
  4. Track ammo/fuel resupply (Hermes cycle) to support continued engagements.

## 2. Panels (no scrolling, 800 × 480)

### Panel A – `Mission Editor`
- **Widgets**
  - Mission queue list (up to 3 planned sorties) with status badges (Ready, Pending, Editing).
  - Mission detail form:
    - Target selection (grid or threat ID).
    - Desired loadout (Sea Dart reload, CAP, strike).
    - Launch window / hold timer.
    - Notes/ROE.
  - Buttons: `Save`, `Delete`, `Duplicate`, `Assign to flight`.
- **Dependencies**
  - Threat list from RADAR/WPN (for quick target selection).
  - Supply status from ENG (available reloads).
- **Interaction notes**
  - Use segmented steps; highlight required fields.
  - Provide preset buttons (“CAP over AA14”, “Strike exocet source”) for speed.

### Panel B – `Launch & Resupply`
- **Widgets**
  - Flight deck readiness board:
    - SHAR 1/2, Sea King slots with readiness (Fuel %, Ammo %, Crew).
    - Selected mission assignment badge.
  - Command buttons per aircraft: `Launch`, `Hold`, `Scrub`, `Reload/Refuel`.
  - Hermes resupply timers (ammo pallets, CAP rotation).
  - Optional voice prompt log (last 2 orders).
- **Dependencies**
  - Mission assignments from Panel A.
  - Cooldown/resupply from backend Hermes model.
- **Interaction notes**
  - Launch button large (≥70 px) to avoid mis-press.
  - On launch, show 3-second confirmation overlay (“SHAR-1 catapulted”).

### Panel C – `Active Flights`
- **Widgets**
  - Table of airborne assets (SHAR, Sea King):
    - Callsign, mission, grid, fuel %, ammo %, eta to target/RTB, status (On station, Winchester, Bingo).
    - Action buttons: `Retask`, `RTB`, `Escort`, `Jettison` (as appropriate).
  - Flight timeline mini-graph (optional) to highlight upcoming returns.
- **Dependencies**
  - Live telemetry from runtime CAP manager.
  - Mission data for retask options.
- **Interaction notes**
  - Highlight earliest RTB in amber.
  - When retasking, open inline modal limited to same panel (no navigation).

## 3. Layout Sketch (Panel B)
```
┌── Flight Deck (200 px) ─────────────────────────────┐
│ SHAR-1 READY  Fuel 100% Ammo 100% [Launch] [Hold]   │
│ SHAR-2 RELOAD Fuel  80% Ammo  40% [Reload]          │
│ SEA KING READY Fuel  90% Cargo 2x Ammo [Launch]     │
├── Mission assignment strip (80 px) ─────────────────┤
│ Assigned: CAP AA14 @ 12:45Z (from Mission Editor)   │
├── Hermes cycle (120 px) ────────────────────────────┤
│ Ammo pallet ETA 03:20  |  Next CAP ready 05:00      │
└── Feedback (80 px) ─────────────────────────────────┘
│ “SHAR-1 launch confirmed – monitoring fuel”         │
```

## 4. Data & State
- **Fields**
  - Flight status: Ready, Re-arming, Launching, Airborne, RTB, Down.
  - Mission attributes: target grid, type (CAP/Strike/Resupply), ordnance.
  - Telemetry: fuel %, ammo %, time on station, ETA to RTB.
  - Resupply timers: Hermes ammo/resupply countdowns.
- **Update cadence**
  - Snapshot poll 2 s.
  - Launch/resupply commands optimistic with backend confirmation.
  - Mission editor updates saved immediately; show dirty state if unsaved.
- **State transitions**
  - Assigning mission moves sortie from queue to launch panel.
  - Launch moves aircraft to Active Flights; readiness board updates to “Launching” then “Airborne”.
  - Retask updates mission profile and notifies RADAR/WPN (for target awareness).

## 5. Alerts & Feedback
- Launch success: green banner “SHAR-1 in the air”.
- Launch failure: red banner with cause (deck foul, no fuel).
- Mission save: subtle confirmation “CAP AA14 saved”.
- Flight warnings: amber for Bingo fuel, red for Winchester/heavy damage.
- Audio cues: optional catapult launch sound, RTB callouts.

## 6. Cross-Station Links
- `View target` button links to RADAR ➜ Hostiles.
- `Weapon resupply` status syncs with ENG station (ammo pallets).
- Launch events logged in LOG for action review.

---

# Station Spec · ENG (Engineering)

## 1. Purpose & Critical Actions
- **Role**: Monitor and repair damage across HMS Sheffield and HMS Hermes; allocate limited repair teams.
- **Time-critical actions**
  1. Read Sheffield status at a glance (systems, compartments, critical alerts).
  2. View Hermes health to coordinate resupply/flight ops; highlight critical damage.
  3. Assign repair teams to highest priority systems, track repair timers, and reallocate as needed.

## 2. Panels (no scrolling, 800 × 480)

### Panel A – `Sheffield Status`
- **Widgets**
  - System grid (Propulsion, Fire Control, Radar, Hull, Damage Control, etc.) with state badges (OK, Damaged, Offline).
  - Compartment map (simplified top-down) showing flooding/fire icons.
  - Alert list (e.g., “Fire amidships: 01:20 to spread without response”).
  - Hull integrity meter + threshold alarms.
- **Dependencies**
  - Damage snapshot from runtime engineering model.
- **Interaction notes**
  - No scrolling; system grid in two columns (each cell 120×80 px).
  - Tap a system to open assignment modal (connects to Panel C) or show contextual advice (“Needs Fire Team”).

### Panel B – `Hermes Status`
- **Widgets**
  - Stylised Hermes contour diagram with deck zones (Fore, Mid, Aft) and system badges (Flight Deck, Hangar, Fuel, Ammo, RADAR).
  - Lives counter / health bar (e.g., 6/8).
  - Deck readiness indicator (Green/Amber/Red) tied to COMMS availability.
  - Active damage cues (blinking overlays).
- **Dependencies**
  - Hermes health data from runtime.
- **Interaction notes**
  - Diagram sized ~320×220 px; use color-coded overlays.
  - Provide legend (Green OK, Amber risk, Red critical).

### Panel C – `Repair Allocation`
- **Widgets**
  - Team roster (Team Alpha, Bravo, Charlie) with availability + fatigue meter.
  - Assignment list:
    - System, damage level, required team type (Fire/Electric/Structural), repair timer countdown.
  - Controls: `Assign`, `Reassign`, `Pause`, `Rush` (rush shortens timer, increases fatigue).
  - Queue / suggestions (“Recommended: Repair Radar in 02:30”).
- **Dependencies**
  - Sheffield and Hermes damage states (for recommended actions).
- **Interaction notes**
  - Assign flow: tap system → choose team → confirm (all within panel).
  - Timers update live (1 s).

## 3. Layout Sketch (Panel C)
```
┌── Teams (120 px) ────────────────────────────┐
│ Alpha READY  Fatigue 10%                     │
│ Bravo BUSY  (Radar)  02:10 remaining         │
│ Charlie READY  Fatigue 30%                   │
├── Assignments (240 px) ──────────────────────┤
│ Radar – Offline – Needs Electronics Team     │
│ Hull – Flooding – Needs Structural Team      │
│ Engine – Damaged – Needs Mechanics           │
├── Actions (120 px) ──────────────────────────┤
│ [Assign] [Reassign] [Pause] [Rush]           │
└── Alerts (40 px) ────────────────────────────┘
│ “Fire midships spreading in 01:20”           │
```

## 4. Data & State
- **Fields**
  - System states: OK, Damaged, Offline, On Fire, Flooded.
  - Repair timers: seconds remaining, pause/rush modifiers.
  - Team status: Ready, Busy, Resting; fatigue level expressed 0–100.
  - Hermes health: lives, deck readiness, active damage.
- **Update cadence**
  - Snapshot poll 2 s; timers tick locally each second.
- **State transitions**
  - Assigning team starts timer; on completion state flips to OK.
  - Rush reduces timer by e.g. 30% but increases fatigue; show warning if fatigue >80%.
  - If damage escalates (e.g., fire spreads), highlight in red and push notification.

## 5. Alerts & Feedback
- Major damage: red banner (e.g., “Hull breach deck 2!”).
- Repair completion: green “Radar restored”.
- Team exhaustion: amber “Team Bravo exhausted – rest recommended”.
- Hermes critical threshold (≤2 lives) triggers persistent warning.

## 6. Cross-Station Links
- `Request resupply` button sends COMMS notification (Sea King ammo runs).
- Weapons station receives ammo reload status as repairs complete.
- LOG station records damage events and repair completions for audit.

---

# Station Spec · LOG (Events & Comms)

## 1. Purpose & Critical Actions
- **Role**: Provide chronological record of all significant events (orders, sensor reports, damage, launches) for audit and coordination.
- **Time-critical actions**
  1. Quickly filter to relevant category (NAV, RADAR, WPN, COMMS, ENG).
  2. Review latest alerts/messages without leaving station context.
  3. Pin or acknowledge critical items for follow-up.

## 2. Panels (no scrolling, 800 × 480)

### Panel A – `Recent Feed`
- **Widgets**
  - Scroll-less feed showing last 8 events (auto-refresh).
  - Event line structure: timestamp, category badge, message text, related station shortcut.
  - Quick filter chips (ALL, NAV, RADAR, WPN, COMMS, ENG, SYSTEM).
- **Dependencies**
  - Event stream from backend log manager.
- **Interaction notes**
  - Use compact 48 px rows; highlight category color-coded.

### Panel B – `Pinned & Alerts`
- **Widgets**
  - List of operator-pinned events (max 4).
  - Persistent critical alerts (e.g., “Hermes at 2 lives”, “CAP fuel bingo”).
  - Buttons: `Pin`, `Clear`, `Export` (optional screenshot/log save).
- **Dependencies**
  - Shared store for pinned IDs (per console user).
- **Interaction notes**
  - Pin action available in Panel A; Panel B only displays/manage pins.

### Panel C – `Search & Export`
- **Widgets**
  - Time range picker (last 5 min / 30 min / mission).
  - Keyword search field + `Go`.
  - Results table (5 entries at a time) with copy/share button.
  - `Export to USB` or `Send to printer` options (optional for scenario playback).
- **Dependencies**
  - Full log history accessible via API.
- **Interaction notes**
  - Keep search controls on top; results limited to avoid scroll. For longer data, show “Open in external viewer”.

## 3. Layout Sketch (Panel A)
```
┌── Filters (60 px) ─────────────────────────────┐
│ [ALL] [NAV] [RADAR] [WPN] [COMMS] [ENG] [SYS]  │
├── Event list (360 px) ─────────────────────────┤
│ 12:41:32 NAV  Course set to 045°               │
│ 12:41:35 RADAR Locked Tiger-21                 │
│ 12:41:40 WPN  Sea Dart fired (HIT)             │
│ …                                              │
├── Footer (60 px) ──────────────────────────────┤
│ “Auto-refresh ON (2 s)”   [Pause]              │
└───────────────────────────────────────────────┘
```

## 4. Data & State
- **Fields**: timestamp (HH:MM:SS), category, message body, severity (info/warn/crit), related entity (optional).
- **Update cadence**: 2 s poll; manual `Pause` stops auto-refresh for review.
- **State transitions**: Pin toggles pinned list; clearing alert moves it to history but retains in raw log.

## 5. Alerts & Feedback
- Critical events display red badge and stay visible until acknowledged.
- Pin action gives subtle confirmation “Pinned Sea Dart hit”.
- Export success message (green “Log saved to USB”).

## 6. Cross-Station Links
- Tapping category badge opens corresponding station.
- WPN/ENG events link to Shots/Repair panels.
- COMMS events link to Active Flights.
