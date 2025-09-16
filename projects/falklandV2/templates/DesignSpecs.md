Got you. I’ve rebuilt the whole thing as one consistent master doc with two layers inside it: a strict spec Codex can bind to, plus the full-fat human design (examples, audio, narrative). For the station UIs I’ve switched from HTML to a desktop-app UI spec: panes, fields, indicators, actions, and state flows described in plain language so Codex won’t “invent” a web server.

Falkland V3 — Dual Reference (Strict Spec + Full Design)

Part I — Strict Spec (Codex Reference)

This section is the single source of truth for constants, rules, and interfaces. All numbers and logic here must match code. If anything differs elsewhere, this section wins.

Core world model

Engine grid is 40×40 at 1 nm per cell. The captain’s “threat board” is 30×30 at 1 nm per cell, centred within the engine grid. Off-board activity can be modelled as countdowns and only joins the bubble around 12–15 nm. The default ship start is K15 unless overridden by a scenario. HMS Hermes and HMS Coventry are always tracked and always visible but never count toward the radar’s 10-contact cap. The game clock is real-time; the engine tick is 1 second. The code word “status” advances the game hour by exactly +1 (H+1). Maximum movement per game hour is one grid cell even at maximum speed. Course changes take 1 second per degree. Own ship max speed is 32 knots. Damage uses hidden “lives”: Sheffield has 4, Hermes has 8. Each hit reduces lives by 1. UI health is shown as a percentage derived from lives (Sheffield 4 lives = 100%; Hermes 8 lives = 100%). At 0 lives the UI shows 0% and the abandon-ship routine triggers.

Stations and roles

RDR is the Radar Officer and owns scanning and tracking. WPN/FCR is the Weapons Officer and owns arming, locking, and firing. NAV is the Navigation Officer and owns course, speed, and convoy following. COMMS is the Communications Officer and owns Hermes link and CAP tasking. ENG is the Engineering Officer and owns system status and repairs.

Radar (RDR)

Automatic scan runs every 180 seconds; manual scan triggers an immediate sweep. At most 10 active contacts (friendly or enemy) are displayed and managed by radar; Hermes and Coventry are permanently tracked outside this cap. Spawn distance is 15–20 nm from own ship. On a 1d6 roll of 5, one contact may appear by surprise at 10 nm. Exception: Super Étendard with Exocet always originates at 20 nm or beyond and never uses the 10 nm surprise. Hostile courses are biased toward own ship. Contacts move at 75% of real-world maximums. Hostile course and speed may change every 5 minutes. The priority target is determined by highest threat; ties break by nearest range, then by earliest detection time. Clock positions are computed relative to ship heading using 30-degree sectors. Alert triggers when the priority target is within 3 nm; a Red escalation at 1 nm is permitted. Contacts that leave the world bubble are dropped; destroyed contacts are logged and removed from the active list.

Weapons and fire control (WPN/FCR)

A primary target lock is exclusive; the system must unlock before locking a different target. Live fire requires the weapon to be armed, a primary lock to exist, the target to be inside the weapon’s range, and the weapon class to match the target class; otherwise the fire request is rejected or results in a miss as defined by the weapon rules. Test fire consumes ammunition but needs only “armed”; no target is required. The engagement cycle is Arm → Lock → Fire. UI state exposes, per weapon: weapon type, remaining ammunition, min/max range, “target in range and appropriate” indicator, “armed” indicator, and Fire/Test actions.

Sheffield gameplay loadout (intentional alt-history)

Sea Dart SAM: 26 missiles, engagement 2–35 nm, best against medium/high altitude aircraft, strongly reduced at very low altitude.
4.5-inch Mk.8 gun: HE 550 and Illumination 100; effective 1–12 nm for surface/shore.
20 mm Oerlikon: 0.25–0.5 nm; aircraft/helos; low hit probability.
Two GAM-BO1 20 mm: 0.3–2.5 nm; aircraft/helos; low hit probability.
Exocet MM38: 4 missiles, engagement 7–23 nm, surface only.
Corvus chaff: 15 salvoes.

Flight-time rules in real seconds are: shells and bullets take two seconds per nautical mile of range; guided missiles take four seconds plus six seconds per nautical mile of range.

Communications (COMMS) and CAP

Sea Harriers operate in pairs. Each aircraft carries four AIM-9. Effective missile envelope is roughly 2–5 nm with higher probability closer in. Launch cadence is one pair per 180 seconds; surge launches up to two pairs inside 300 seconds. A maximum of six aircraft can be on task at once, briefly rising to eight with penalties. Station radius for on-station effects is 5 nm; intercept checks occur when a hostile comes within 15 nm of the pair. A pair that is already airborne, has missiles remaining, and is within 15 nm of a locked target can be re-vectored immediately. Turnaround from recovery to “ready” is always five minutes. Winchester pairs (no missiles) return to base. When tasked to intercept or hold station, pairs request permission to engage when conditions are met; they ask every 30 seconds and return to base after ten minutes if engagement is not authorised.

Engineering (ENG)

The seven critical systems are Navigation, Radar, Fire Control/Weapons, COMMS, Engine/Propulsion, Rudder/Steering, and Hull (whose sub-states are breach and fire). There are four repair teams. A team can be assigned to one damaged system at a time. Each damaged system has a two-minute repair timer when a team is committed. If a critical system is left unrepaired for two minutes after damage, the system becomes permanently inoperative and lives are reduced by one. Repaired systems return to service once their timer completes. At zero lives (0%) the abandon-ship routine triggers.

End states and missions

At 0% health, the abandon-ship alarm sounds and the game ends once the captain acknowledges. Missions and scenarios can be loaded at the Menu Station; missions define victory conditions and rewards. Hermes is always tracked and must be protected; losing Hermes ends the game immediately.

⸻

Part II — Full Design Doc (Human Use)

This section expands the strict spec with examples, audio, language for the crew, and operator guidance. All numbers mirror the strict spec above.

World and timing

You sail a 30×30 tile “threat board” at 1 nm per tile, sitting inside a 40×40 logic grid. Off-board raids and surface groups can be teased as “countdown tracks” until they enter at roughly 12–15 nm. The real-time clock runs continuously; the radio voice uses H-codes when you ask for “status.” Every status call advances one game hour and caps actual displacement to one tile even if you’re flat out at 32 knots. Helm changes heading at one second per degree; that matters in knife-fights when shaving a rocket run.

Stations and voices

RDR talks like a plotter: bearings, ranges, relative clocks. WPN/FCR is clipped and procedural, owning the “Arm–Lock–Fire” cadence and confirming locks. NAV calls your grid, heading and speed, and warns when you are one tile from the board edge. COMMS handles Hermes, CAP launches, “Winchester,” and “RTB.” ENG calls system hits, sets repair teams, and pressures you if a system is bleeding out toward a permanent loss.

Radar behaviour (RDR, lived-in)

Routine sweeps ping every three minutes, but an immediate scan request can fold in a contact between sweeps. The list never holds more than ten; that keeps the bridge sane. Hermes and Coventry are pinned on their own layer: always there, never pushing real targets off the list. Spawns begin at 15–20 nm. If fortune frowns and the die comes up a five, something may pop at ten miles—never a Super Étendard with Exocet; they stay at twenty and beyond. Hostiles bend their tracks toward you but not suicidally; they move at around three-quarters of their real maxima, and can jink every five minutes. RDR reports clock positions relative to your present heading, not north. Expect “Priority target, three o’clock, 18 miles, high threat.” If that priority crosses three miles, the bridge gets a warning; under a mile, the tone hardens and the weapons lights should already be green.

Worked micro-example: a Mirage spawns at 18 nm bearing 120° while you’re steering 090°. Relative is 30°, which is one o’clock; RDR calls “one o’clock, eighteen miles.” If you swing right to 120°, the same track shifts to twelve o’clock; your clock is your nose.

Audio cues: a soft bridge loop should idle at low volume. When a weapon goes “green” because the primary is in envelope, a discrete chirp confirms you could shoot without drowning the voice-net. A harsh fly-by crack is reserved for very close passes at two tenths of a mile.

Weapons pacing (WPN/FCR, lived-in)

Fire control owns one hard lock at a time. If you want CAP to chase someone you haven’t decided to shoot yet, you still lock it; CAP vectors off your primary. Test firing is allowed in peacetime exercises and deducts ammunition, but it only needs the weapon armed. Live firing needs the full quartet: armed, locked, in range, right class. Shells are honest: two seconds per mile. A 4 nm ranging shot splashes in eight seconds; at eight miles it’s sixteen. Sea Dart rides the beam: four seconds to launch and settle, then six seconds per mile. Ten miles is roughly sixty-four seconds. Exocet is the same flight-time rule in this model, but only counts for surface, and its minimum range is seven miles in this game. The 20 mm mounts are last-ditch; expect a second or four from first squeeze to effect.

Crew language: “Sea Dart away.” At mid-course: “Missile tracking.” Final call: either “Splash” (if a kill) or “Missile missed; bandit pressing.” For guns: “Gun—shoot.” Optional “Splash” just before impact if you want theatre.

COMMS and air cover

Hermes is a steady presence. Sea Harriers operate in twos, each with four Sidewinders. If a pair is already airborne and within fifteen miles of your locked target, COMMS can re-vector them—“Hermes: CAP pair turning hot, time-on-target one minute.” Otherwise the deck cycles a ready pair in about three minutes; in a surge you can push two pairs inside five. Six on station is sustainable; eight is brief and exacts a price. Pairs patrol a five-mile station circle. If a hostile sniffs inside fifteen miles of them, they request permission to engage, ask again every thirty seconds if you deny, and return to base after ten minutes if they still haven’t fired. Turnaround back to “ready” is five minutes, regardless of who’s shouting. When Winchester, they don’t bluff; they come home.

Behavioural spice you can keep or tune later: challenged attackers sometimes lose their nerve—ten to twenty percent abort when a pair lights them up. If Hermes is hurt enough to close the deck, CAP requests auto-fail until you get the deck clear again.

Engineering and damage

Seven places can take a real bite: Navigation, Radar, Fire Control and Weapons, COMMS, Engine/Propulsion, Rudder/Steering, and the Hull, which can be on fire or open to the sea. You have four repair teams; that’s never enough in a bad ten minutes. Commit one to a system and a two-minute timer starts; pull them off and you lose the time. Leave a system unrepaired for two minutes and it dies for good—and you shed a life on top. At zero lives, the ship is done. ENG should pressure you whenever a timer is halfway spent without a team or a team is about to time out.

Endgame flavour

At zero, the siren cuts through everything. “Captain, recommend abandon ship.” If you acknowledge, the voices fade, panels die, and the farewell text appears. Keep the text you drafted; it lands.

Missions, scenarios, and menu

Use Menu to load scenarios for debugging rather than waiting for the dice. Missions stack win conditions on top: keep Sir Galahad safe, hold an air-defence screen for twenty minutes, coordinate a CAP station at G-10 and live through it, or go surface hunting and force Belgrano to quit after one Exocet hits. Mission clocks and rewards are your theatre here.

Audio backbone

Bridge ambience should never drown callouts. Weapon-ready chirps are short and polite. The Mk.8 has a distinct bark; Sea Dart launch is a whoosh with a hard impact if it connects. Exocet gets its own tone. Keep a single “hit_small.wav” for 20 mm, and a “chaff_dispense.wav” that feels like a canister thump and bloom.

⸻

Part III — Station UI Specs (Desktop App)

These are desktop-app oriented UI descriptions. Think “main window with station tabs” or “multi-pane view.” Elements are named so Codex can bind to them without inventing HTML. For each station describe panes, fields, indicators, actions, and state transitions.

Radar Station (RDR)

Layout is a single main pane with a contact table, a status header, and a controls strip. The status header shows the auto-scan countdown in seconds and the current H-code. The contact table lists up to ten rows with columns for contact ID, type, allegiance, clock, range in nm, threat level, speed and heading. Rows are sorted so the closest highest-threat is always row one; if sorting changes between sweeps the table animates to the new order. The primary target is visually flagged with a left-edge stripe and mirrored in a small “Primary” info chip under the header. The controls strip has a Scan button that forces a sweep and a Status button that triggers the H+1 hour advance; pressing Status also refreshes all bearings and ranges and emits the standard status report line. Two alert indicators live in the header: a general alert that turns amber when the priority target is within three miles, and a red alert that turns red at one mile and pulses at 1 Hz until cleared by the user. Hermes and Coventry appear in a small “Convoy” subpane with name and grid; they never occupy rows in the contact table. Destroyed contacts animate out of the list and are logged to a “History” drawer that can be toggled open by the operator.

Weapons & Fire Control Station (WPN/FCR)

Layout uses a weapons table, a lock panel, and an actions strip. The weapons table shows one row per system with the fields weapon name, ammunition remaining, min–max range, a target-in-range indicator that lights green only when the locked target is inside envelope and of a valid class, an armed indicator that lights when the system is armed, and two buttons labelled Fire and Test. The lock panel shows the current primary target with its ID, type, range and a lock status icon; there are two buttons labelled Lock and Unlock. The Unlock action drops any current lock. The Lock action opens a picker listing current contacts by ID and type; selecting one attempts a lock and either confirms with “Lock established” or returns a reason for failure. The actions strip has an Arm toggle per weapon, a global “Safe” switch for drills, and a text log that prints the standard callouts (“Sea Dart away”, “Missile tracking”, “Hit confirmed”, “Missed”). The station enforces the fire rules strictly: Fire is enabled only when armed, locked, in range, and target class matches; Test is enabled when armed. Firing deducts a realistic salvo immediately. When the priority-target indicator from RDR goes green on this station, a quiet ready tone plays once and the in-range cell flashes for one second.

Navigation Station (NAV)

Layout has a course-and-speed input panel, a positioning panel, and a convoy panel. The input panel contains a course field that accepts either degrees or cardinal notation and a GO button, and a speed field in knots with a GO button. Submitting a new course starts a visible “rudder time” countdown that equals one second per degree of change. The positioning panel shows your current grid cell, heading, and speed; it also shows a “board edge” predictor that warns if your current course will leave the 30×30 board within the next hour, with a one-tile early alarm that sounds if you are about to cross. The convoy panel shows Hermes and Coventry with “Following” status, present separation in nautical miles, and a note that escort behaviour lags by thirty seconds; if separation exceeds three nautical miles the status changes to “Struggling to keep up” and colours amber until resolved.

Communications Station (COMMS)

Layout includes a Hermes status header, a CAP status panel, and a tasking panel. The Hermes header shows Hermes grid, course and speed. The CAP status panel shows the number of ready pairs, the number committed, aircraft airborne and on station, and any cool-downs or turnarounds with timers in minutes and seconds; Winchester status for pairs is also surfaced. The tasking panel has two flows. The Intercept flow contains a button labelled Launch Intercept that is enabled only when WPN/FCR reports a valid primary lock; pressing it either re-vectors a nearby airborne pair if within fifteen miles, reporting the time over target, or launches a new pair and starts a deck-cycle timer. The Station flow has a grid input field and a Launch CAP button; pressing it launches a pair to that grid and starts an on-station timer. When a CAP pair reaches a hostile within fifteen miles, the station raises a permission dialog with Engage and Hold buttons; choosing Hold starts a repeating prompt every thirty seconds up to ten minutes, after which the pair returns to base. Turnaround always shows as a five-minute timer on the status panel.

Engineering Station (ENG)

Layout is a systems table, a repair control area, and a health readout. The systems table has one row per critical system: Navigation, Radar, Fire Control/Weapons, COMMS, Engine/Propulsion, Rudder/Steering, and Hull. Each row shows current status (OK, Damaged, Offline), a timer field that counts down when a repair team is assigned, and an Assign/Release button. Assigning a team starts the two-minute repair timer; releasing a team pauses the timer; leaving a damaged system without a team for two minutes marks it as permanently offline and reduces lives by one. The repair control area shows total teams and teams available; attempts to assign beyond four are rejected with a “No teams available” message. The health readout shows Sheffield health and Hermes health as percentages derived from their lives; when a ship drops to 0% the station raises a modal “Abandon ship” prompt for the captain to acknowledge. Fires and breaches on the Hull row are called out as sub-badges and will colour the row red until addressed.

Menu Station (Scenarios and Debug)

Layout presents three cards. The Events Monitor card shows a live scroll of engine events with filters for station, severity, and system. The Scenario Editor card opens a form that allows you to set start positions, inject specific contacts, set weather if you add it later, and pre-seed damage or ammo states; saving produces a scenario file that can be loaded later. The Missions card lists curated missions with their victory conditions; starting a mission overlays its win/lose rules on the HUD and enables its reward screen. Hermes is always tracked and appears even when the radar contact list is full.

⸻

Part IV — Worked Examples and Tables (for quick binding)

Flight times align to the strict formula. A Sea Dart at five miles resolves at roughly thirty-four seconds; ten miles is roughly sixty-four; twenty miles about one hundred twenty-four. A Mk.8 round at four miles resolves in eight seconds; at eight miles in sixteen. Exocet at seven to twenty-three miles maps to roughly forty-six to one hundred forty-two seconds.

Priority selection is deterministic. If two contacts share the same threat and range, the older one remains primary until circumstances change. The 3 nm alert and 1 nm red alert mirror that choice; if primary changes, alerts follow the new primary.

Spawn rules are canonicalized here. Hostiles appear at fifteen to twenty miles; on a DC 5 one may appear at ten; Étendard never uses the ten-mile rule and instead begins at twenty or more. Contacts that step outside the engine grid are dropped silently; kills populate the History drawer.

Lives and UI health mapping is linear. Sheffield converts 4, 3, 2, 1, 0 lives to 100%, 75%, 50%, 25%, 0%. Hermes converts 8 through 0 to 100%, 87.5%, 75%, 62.5%, 50%, 37.5%, 25%, 12.5%, and 0%. The ENG station displays only percentages; Codex uses lives internally.

