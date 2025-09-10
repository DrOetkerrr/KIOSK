# Falkland V3 — Design

## Game layout and gameplay

As captain, you speak with your ensign **Jim Hensen** on board **HMS Sheffield (Type 42 Destroyer)**, patrolling near the Falklands on a map.

- **Captain’s board:** 26×26 grid (columns A–Z, rows 1–26), each cell = **1 nm**. Start position: **K13**.  
- A real-time **game timer** starts at game start.

### Engine vs. captain map
The **engine** runs on a **40×40** logic grid (1 nm per cell). The captain’s playable **26×26** “threat board” is centered within that world. Off-board activity (≈18–40 nm) may be tracked as countdowns and only enters the bubble at ~12–15 nm.

### Key systems (Type 42)
- **Radar:** search and target radar  
- **Fire Control:** one system for **Sea Dart SAM** and the **4.5-inch gun**  
- **Electronic Warfare:** **Corvus** decoy launchers

---

## Player intent

Fluent, convincing conversation with the ensign. The ensign ties dialogue to game logic and **proactively warns** on:
- New radar contact  
- High-threat target within reach of your **longest-range appropriate** weapon  
- One grid cell before leaving the 26×26 board on current course  
- Emergencies on board (enemy damage, system failure)

---

## Navigation

1. Set course (degrees or N/E/S/W).  
2. Set speed.  
3. Receive updates on position (grid cell), course, and speed. Border → **alarm**.  
4. **HMS Hermes** (flagship) and **HMS Coventry** (Type 21) remain within **3 cells**.  
5. Hermes/Coventry live **outside** the 10-contact limit and are **always present**.  
6. They **mimic** your course/speed with a **30 s** delay.

---

## Radar

- Routine scan every **3 minutes** (may trigger spawn).  
- Report any **new contacts**.  
- Contacts spawn from a **weighted JSON list**; each has **type** (vessel/aircraft/missile), **allegiance** (friendly/neutral/hostile), **threat** (harmless/low/medium/high), **speed**, **heading**, **grid**.  
- The **closest highest-threat** contact is the **priority target** (index **1** in list).  
- **Alarm** if priority target within **3 nm**.  
- **Max 10 active contacts**.  
- **Spawn distance rule:** spawn **outside 15–20 nm**; occasional surprises at **10 nm on DC 5 (1d6)**.  
- Hostile courses biased **toward our ship**; contacts move at **75%** of real-world speeds; course/speed may change **every 5 minutes**.  
- Track each contact’s **position, speed, direction**.  
- **Forget** leavers; **remember** destroyed, but remove from active list.  
- Radar can **lock one** target; **unlock** before relocking.  
- Commands: `scan` (immediate sweep), `status` (read-out), `update` (new contacts?), `lock`, `unlock`.  
- **Scanning** is search radar; **locking/firing** uses fire-control radar.

### Contact list (excerpt; JSON-ready)
Typo fixes applied (e.g., **Pucará** quoted correctly). Friendly entries may use `NaN` for Min/Max Range when not applicable.

```json
[
  {"Name":"A-4 Skyhawk","Allegiance":"Hostile","Speed (kts)":385,"Primary Weapon":"Bombs / 20 mm cannon","Min Range (nm)":0.5,"Max Range (nm)":1.0,"Weight":5},
  {"Name":"Dagger (Mirage V)","Allegiance":"Hostile","Speed (kts)":420,"Primary Weapon":"Bombs / 30 mm cannon","Min Range (nm)":0.8,"Max Range (nm)":1.2,"Weight":4},
  {"Name":"Mirage III","Allegiance":"Hostile","Speed (kts)":455,"Primary Weapon":"Bombs / rockets","Min Range (nm)":1.0,"Max Range (nm)":2.0,"Weight":3},
  {"Name":"Pucará","Allegiance":"Hostile","Speed (kts)":196,"Primary Weapon":"Rockets / 20 mm cannon","Min Range (nm)":0.2,"Max Range (nm)":0.8,"Weight":2},
  {"Name":"Super Etendard","Allegiance":"Hostile","Speed (kts)":434,"Primary Weapon":"Exocet AM39","Min Range (nm)":5.0,"Max Range (nm)":35.0,"Weight":1},
  {"Name":"Canberra bomber","Allegiance":"Hostile","Speed (kts)":336,"Primary Weapon":"Bombs","Min Range (nm)":1.0,"Max Range (nm)":2.0,"Weight":1},
  {"Name":"ARA General Belgrano","Allegiance":"Hostile","Speed (kts)":22,"Primary Weapon":"6-inch main battery","Min Range (nm)":8.0,"Max Range (nm)":15.0,"Weight":1},
  {"Name":"HMS Invincible","Allegiance":"Friendly","Speed (kts)":20,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":1},
  {"Name":"Type 42 Destroyer","Allegiance":"Friendly","Speed (kts)":22,"Primary Weapon":"Sea Dart SAM","Min Range (nm)":2.0,"Max Range (nm)":35.0,"Weight":3},
  {"Name":"Type 22 Frigate","Allegiance":"Friendly","Speed (kts)":22,"Primary Weapon":"Seawolf SAM","Min Range (nm)":0.5,"Max Range (nm)":6.0,"Weight":3},
  {"Name":"Amphibious Ship (Fearless/Intrepid/Sir Galahad)","Allegiance":"Friendly","Speed (kts)":12,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":2},
  {"Name":"Fleet Tanker (RFA)","Allegiance":"Friendly","Speed (kts)":14,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":2},
  {"Name":"Stores Ship (RFA)","Allegiance":"Friendly","Speed (kts)":14,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":2},
  {"Name":"Merchantman (Neutral)","Allegiance":"Neutral","Speed (kts)":12,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":1},
  {"Name":"Fishing Trawler (Neutral/Uncertain)","Allegiance":"Neutral","Speed (kts)":10,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":1},
  {"Name":"Lynx Helicopter","Allegiance":"Friendly","Speed (kts)":90,"Primary Weapon":"AS.12 missiles","Min Range (nm)":1.0,"Max Range (nm)":6.0,"Weight":1},
  {"Name":"Sea King Helicopter","Allegiance":"Friendly","Speed (kts)":90,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":1},
  {"Name":"Gazelle Helicopter","Allegiance":"Friendly","Speed (kts)":80,"Primary Weapon":"Light rockets / MG","Min Range (nm)":0.2,"Max Range (nm)":1.0,"Weight":1},
  {"Name":"Landing Craft (LCU/LCVP)","Allegiance":"Friendly","Speed (kts)":7,"Primary Weapon":"n/a","Min Range (nm)":NaN,"Max Range (nm)":NaN,"Weight":2},
  {"Name":"Sea Harrier FRS.1","Allegiance":"Friendly","Speed (kts)":420,"Primary Weapon":"AIM-9 Sidewinder / 30 mm cannon","Min Range (nm)":0.5,"Max Range (nm)":15.0,"Weight":3}
]

Weapons (player ship)
	•	1 × GWS.30 Sea Dart SAM (twin-arm launcher, below-deck magazine)
	•	Valid targets: aircraft (medium/high altitude; reduced effectiveness at very low level)
	•	Game range: ~2–35 nm
	•	Magazine: 26 missiles
	•	1 × 4.5-inch (114 mm) Mk.8 naval gun (forward)
	•	Valid targets: surface ships, shore bombardment, test fire
	•	Game range: 12 nm
	•	1 × 20 mm Oerlikon — 0.25–0.5 nm; aircraft/helos/test fire (low hit chance)
	•	2 × 20 mm GAM-BO1 — 0.3–2.5 nm; aircraft/helos/test fire (low hit chance)
	•	4 × Exocet MM38 (ship-launched, 1982 era) — 3–22 nm (~300 m/s)

Key ammunition
4.5-inch: 550 HE, 100 illumination. Sea Dart: 26. 20 mm: 5000 rounds. Exocet: 4. Corvus chaff: 15 salvoes.

Captain actions
	•	Ask for loadout
	•	Ask if a system is online (manned, loaded, armed, ready)
	•	Arm a weapon system
	•	Test fire (armed; no target needed)
	•	Fire (armed + locked target). If target is beyond max range → guaranteed miss.
	•	Engagement cycle: Arm → Lock → Fire

⸻

Sounds (backbone)
	1.	Looping bridge.wav at low volume; must not crowd other sounds.
	2.	Weapon goes green (in range) → small warning.
	3.	Gun fired → gun sound (no miss sound).
	4.	Sea Dart fired → missile sound; hit → hit sound; miss → splash.
	5.	20 mm fired → light gun sound; on hit → hit sound; on miss → no miss sound.
	6.	Chaff dispensed → chaff sound.
	7.	Enemy aircraft within 0.2 nm → fly-by sound.

⸻

Engagement logic

Flight-time (real seconds)
	•	Shells/bullets: time = range_nm × 2
	•	Missiles (Sea Dart / Exocet): time = 4 + (range_nm × 6)

Worked examples

4.5-inch gun (game: 12 nm)
	•	4 nm → 8 s
	•	8 nm → 16 s
Callouts: “Gun fired!” → result at time (optional “Splash!” just before).

Sea Dart SAM (2–35 nm)
	•	5 nm → 34 s (4 + 5×6)
	•	10 nm → 64 s
	•	20 nm → 124 s
Callouts: “Sea Dart away!” → midpoint “Missile tracking…” → result.

20 mm Oerlikon (0.25–0.5 nm) — 0.5–1 s to effect
20 mm GAM-BO1 (0.3–2.5 nm) — ~1–4 s to effect
Exocet MM38 (3–22 nm) — 34–124 s; include “Seeker lock, terminal run” ~10 s before result

⸻

Hermes CAP

You can request a Sea Harrier CAP to engage your locked target.

Captain-facing rules (refinement)
	•	If a SHAR pair is already airborne, has missiles, and is within 15 nm of the locked target when you call CAP, re-vector that pair; report time-over-target (distance / 420 kts).
	•	Otherwise, if Hermes has a ready pair, launch that pair (2× AIM-9 per aircraft; pairs operate together).
	•	Standard launch: 1 pair / ~3 min (deck cycle).
	•	Surge: up to 2 pairs inside ~5 min.
	•	Simultaneous on task: cap 6 (brief 8 with penalties).
	•	Turnaround: ~25 min to rearm/refuel a pair to “ready”.

CAP mission spec (state & effects)
	•	Config (cap_config):
aircraft_type, cruise_speed_kts: 420, deck_cycle_per_pair_s: 180, max_ready_pairs: 2, airframe_pool_total: 8,
default_onstation_min: 20, bingo_rtb_buffer_min: 4, scramble_cooldown_min: 10, station_radius_nm: 5,
effects.spawn_weight_multiplier (type based), effects.intercept_prob_pre_release (type based),
effects.defence_bonus_if_not_intercepted: 0.10
	•	Lifecycle: queued → airborne → onstation → rtb → complete
	•	Effects while onstation (within radius):
	•	Hostile spawn weights reduced (e.g., Skyhawk/Dagger 0.6; Mirage 0.7; Pucará 0.5; Étendard/Canberra 0.8)
	•	Pre-release intercept check (e.g., Skyhawk/Dagger 0.50; Mirage III 0.45; Pucará 0.70; Super Étendard 0.30; Canberra 0.40)
	•	If intercept succeeds → remove attack; radio “CAP splash …”
	•	If intercept fails → apply +10% defence bonus this minute; radio “CAP engaged, attacker pressed on.”
	•	Morale abort: 10–20% chance a hostile aborts when challenged by CAP (type-tunable)
	•	Resources: airframe pool (8), ready pairs (2), cooldowns, 25-min turnaround. Losses/damage reduce availability.
	•	Logging: approval/denial with reason; “Hermes: CAP pair launching”; “CAP on station …”; “CAP RTB”; “CAP recovered …”

    {"Name":"HMS Hermes","Allegiance":"Friendly","Speed (kts)":20,"Primary Weapon":"Sea Harrier (air defence)","Min Range (nm)":NaN,"Max Range (nm)":NaN}

     Defence (enemy attacks)
	•	Attack loop: each game minute, hostiles in envelope roll to attack; apply defences (Sea Dart, guns, chaff, manoeuvre).
	•	Enemy Exocet budget (scenario): 2 total.
	•	Sea Dart may attempt pre-terminal hard-kill inside ~3–4 nm.
	•	Chaff + hard turn: two-step soft-kill (first 50–60% seduction; if missile presses, apply −10% hit penalty).
	•	Guns: slim last-ditch chance inside 1 nm.
	•	Hermes as target: hostiles may prefer Hermes; if Hermes sunk or deck closed, CAP requests auto-fail.
	•	CAP attrition: intercepts carry small loss/damage chance; losses reduce pool; damaged pairs require turnaround.

Example attack/defence JSON (Sea Dart terminology)

{
  "version":"1.0",
  "notes":"Probabilities are decimal 0.0–1.0. Clamp final hit ≥ 0.05. Ranges in nm.",
  "attack_types":[
    {
      "id":"bomb_lowlevel",
      "name":"Low-level bomb run (A-4 / Dagger / Mirage)",
      "base_hit":0.4,
      "range_hint_nm":[0.5,1.0],
      "defences":[
        {"id":"sea_dart_this_window","label":"Sea Dart fired this window","delta":-0.2},
        {"id":"ciws_close","label":"20mm/GAM-BO1 effective at ≤1 nm","delta":-0.1,"range_le_nm":1.0},
        {"id":"gun_barrage","label":"4.5\" barrage","delta":-0.1},
        {"id":"hard_turn_flank","label":"Hard turn + flank speed","delta":-0.1}
      ],
      "damage_profile":{"Light":0.333,"Moderate":0.333,"Severe":0.167,"Critical":0.167}
    },
    {
      "id":"rocket_strafe",
      "name":"Rocket/cannon strafing (Pucará / Mirage)",
      "base_hit":0.3,
      "range_hint_nm":[0.2,2.0],
      "defences":[
        {"id":"ciws_close_heavy","label":"20mm/GAM-BO1 at ≤1 nm (heavy)","delta":-0.2,"range_le_nm":1.0},
        {"id":"ciws_mid","label":"20mm/GAM-BO1 at ≤2 nm","delta":-0.1,"range_le_nm":2.0},
        {"id":"gun_barrage","label":"4.5\" barrage","delta":-0.1},
        {"id":"hard_turn_speed","label":"Hard turn + speed","delta":-0.1}
      ],
      "damage_profile":{"Light":0.5,"Moderate":0.333,"Severe":0.167,"Critical":0.0}
    },
    {
      "id":"bomb_altitude",
      "name":"High/medium-altitude bombs (Canberra)",
      "base_hit":0.3,
      "range_hint_nm":[1.0,2.0],
      "defences":[
        {"id":"gun_barrage","label":"4.5\" barrage","delta":-0.1},
        {"id":"sea_dart_snapshot","label":"Sea Dart snapshot","delta":-0.1},
        {"id":"hard_turn_speed","label":"Hard turn + speed","delta":-0.1}
      ],
      "damage_profile":{"Light":0.333,"Moderate":0.333,"Severe":0.167,"Critical":0.167}
    },
    {
      "id":"missile_seaskimmer",
      "name":"Sea-skimming missile (Exocet class)",
      "base_hit":0.5,
      "range_hint_nm":[3.0,22.0],
      "defences":[
        {"id":"ciws_very_close","label":"20mm/GAM-BO1 at ≤1 nm","delta":-0.1,"range_le_nm":1.0},
        {"id":"hard_turn_speed","label":"Hard turn + speed","delta":-0.1}
      ],
      "damage_profile":{"Light":0.111,"Moderate":0.333,"Severe":0.25,"Critical":0.306},
      "prerequisite_decoy":{"id":"chaff","label":"Chaff seduction before hit roll","Pc_default":0.5,"Pc_with_active_cloud":0.6}
    },
    {
      "id":"surface_gunnery_6in",
      "name":"Surface gunnery (6\" battery, e.g., Belgrano)",
      "base_hit":0.5,
      "range_hint_nm":[8.0,15.0],
      "defences":[{"id":"hard_turn_speed","label":"Hard turn + speed","delta":-0.1}],
      "damage_profile":{"Light":0.333,"Moderate":0.333,"Severe":0.167,"Critical":0.167}
    }
  ],
  "global_rules":{
    "min_hit_floor":0.05,
    "critical_system_selection":{
      "mode":"uniform",
      "systems":[
        {"id":"radar","label":"Radar (air/surface search)","weight":1},
        {"id":"fire_control","label":"Fire control / attack radar","weight":1},
        {"id":"engine","label":"Engine/propulsion","weight":1},
        {"id":"rudder","label":"Rudder/steering","weight":1},
        {"id":"hull","label":"Hull breach","weight":1},
        {"id":"fire","label":"Fire","weight":1}
      ]
    },
    "system_effects":{
      "radar":{
        "degraded":{"defence_modifier_penalty":-0.05,"notes":"Reduce Sea Dart/barrage effectiveness."},
        "offline":{"sea_dart_disabled":true,"barrage_unavailable":true}
      },
      "fire_control":{
        "degraded":{"barrage_penalty":-0.05},
        "offline":{"barrage_unavailable":true}
      },
      "engine":{"per_step_speed_loss_kts":5,"offline":{"dead_in_water":true}},
      "rudder":{"degraded":{"remove_hard_turn_bonus":true,"future_bomb_rocket_hit_bonus":0.05},"offline":{"course_changes_limited":true}},
      "hull":{"degraded":{"speed_cap_kts":15},"severe":{"flooding_active":true},"critical":{"loss_check_on_boxes":true}},
      "fire":{"active":{"defence_modifiers_extra_penalty":-0.05}}
    }
  }
}
Captain commands (during an attack)
	•	“Fire Sea Dart” → consumes one Sea Dart attempt this window, applies its defensive modifier
	•	“Open fire with 20mm / GAM-BO1” → close-in weapons if range allows
	•	“4.5-inch barrage” → area defence
	•	“Deploy chaff” → once per incoming missile; cloud persistence rules apply
	•	“Hard turn and flank speed” → manoeuvre this minute; modifies hit chance

Between / after attacks
	•	“Assign repair team to X” (radar, fire control, engine, rudder, hull, fire, flooding)
	•	“Reassign team” (costs assign + move time)
	•	“Hold team in reserve”
	•	“Prioritize fire/flooding” (forced before other repairs if present)

Strategic / morale
	•	“Continue action”
	•	“Prepare to abandon ship”
	•	“Cease fire / conserve ammo”

    Radio logic

Hermes is always tracked (outside the 10-contact cap). It’s your duty to protect Hermes. The ensign can always report Hermes’ position.
{"Name":"HMS Hermes","Allegiance":"Friendly","Speed (kts)":20,"Primary Weapon":"Sea Harrier (air defence)","Min Range (nm)":NaN,"Max Range (nm)":NaN}

Missions (examples)
	1.	Naval Gunfire Support (NGFS) — Steam to H-12; fire 25 rounds; hold 15 minutes; RTB F-10. Win: ≥20 on target and remain operational.
	2.	Amphibious Escort (Sir Galahad) — Rendezvous E-9; escort to H-11; maintain ≤2 nm. Win: Sir Galahad intact.
	3.	Air Defence Screen — Hold F-12 for 20 minutes; down/deter ≥2 raids.
	4.	CAP Coordination — Request CAP to G-10; remain within 3 nm; survive 15 minutes with CAP active; ≥1 intercept.
	5.	Surface Hunt (Belgrano) — Steam to D-8; fire ≤2 Exocets. Win: ≥1 hit → Belgrano withdraws.

If the ship is critically hit, abandon ship sounds the alarm and ends the game.

Appendix — Design Notes & Balancing (Living)

Map & physical board
	•	Engine grid: 40×40 (1 nm per cell) for encounters, scenarios, scans
	•	Captain’s threat map: 26×26, centered “player bubble”
	•	Physical map: 2×2 cm cells; ≈52×52 cm (3×3 tiles on 24×24 cm bed)
	•	Off-board spawns: 18–40 nm declared, counted down, enter at ~12–15 nm

Radar & detection
	•	Low-flyer detection (Type 21/42 search): 15–25 nm (sea state/clutter dependent)
	•	Scan cycle: every 3 min; each scan 1d6 DC 5 to spawn hostile
	•	No-spawn bubble: 15–20 nm; occasional 10 nm on DC 5 (1d6)
	•	Spawn surge rule (GM): 1d6 → 1: two contacts; 2–4: none; 5–6: one
	•	Friendly ratio note: as desired; e.g., “for every 3 contacts, 1 may be enemy” (optional filter)

CAP (Sea Harriers, Hermes)
	•	Deck cycle: 180 s per pair (ready)
	•	Re-vector rule: if SHAR pair airborne, has missiles, and ≤15 nm of locked target on call → re-vector; report TOT
	•	Standard launch: 1 pair / ~3 min; surge: up to 2 pairs / ~5 min
	•	Airborne cap: cap 6 on task (brief 8 with penalties)
	•	Turnarounds: ~25 min to return a pair to “ready”
	•	Intercept odds (pre-release): A-4 50%, Dagger 50%, Mirage III 45%, Pucará 70%, S. Étendard 30%, Canberra 40%
	•	Defence bonus if not intercepted: +10% to ship’s defence rolls
	•	Morale abort: 10–20% chance when challenged by CAP

Enemy attacks (summary)
	•	Loop: per minute; in-envelope attackers roll → apply Sea Dart, guns, chaff, manoeuvre
	•	Exocet budget (enemy): 2 total
	•	Sea Dart pre-terminal: hard-kill inside ~3–4 nm
	•	Chaff+Turn: 50–60% seduce; if presses, −10% hit
	•	Guns (last ditch): slim chance at ≤1 nm
	•	Hermes targeting: hostiles may prefer Hermes; Hermes sunk/deck closed → CAP requests auto-fail
	•	CAP attrition: chance of pair loss/damage on intercept

Canary (pre-flight)
	•	Engine imports; tick 10 cycles OK
	•	Contacts ≤ 10
	•	If contacts > 0 → priority assigned
	•	Rules JSON loads (no missing / NaN)
	•	Flight recorder writes canary.heartbeat with session seed

Damage & victory (combined model)
	•	Show mission clock (real time) and game clock (countdown from 30 min)
	•	Graded severities (Light/Moderate/Severe/Critical) with repair teams, escalation, abandon-ship thresholds (as specified)
	•	Lives overlay (tabletop aid):
	•	Regular ships: 4 lives. Bomb/rocket/gun hit → −1 life (unless already sunk by graded model)
	•	Exocet hit: −4 lives (immediate loss) and apply Critical to Hull in graded model
	•	Hermes: 8 lives. At ≤3 lives → deck closed (no CAP). At 0 → Hermes lost
	•	Loss rule: if either model reaches loss (e.g., hull scalar ≥ 6 or lives = 0) → ship lost
	•	Victory: survive 30 minutes and keep Hermes afloat (lives > 0). Losing Hermes ends the game immediately (abandon ship).
