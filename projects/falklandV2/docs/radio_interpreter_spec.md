# Radio Interpreter Spec v0.1

Goals
- Replace key button flows with voice: radar scan/lock, fire weapon, launch CAP, authorize CAP engage.
- Use OpenAI for interpretation + in-character reply; keep Piper for UK radio voices.
- Never rely on pre-cooked lines; every reply is generated from the live context and constraints.
- Deterministic actions: validate and gate anything risky (always confirm fire/engage).

Turn Flow
1) ASR (Whisper local) → user_text.
2) Build context_pack from live game state.
3) Call OpenAI with system+context+user_text; require JSON response (function-calling/JSON mode).
4) Validate actions locally. If needs_confirm or invalid → ask clarifying question. If confirmed/valid → execute.
5) Render `radio` via Piper (role-specific UK voices) with PTT FX baked-in.

Context Pack (sent with each request)
Minimal fields to keep prompts tight and cheap.
```
{
  "who": {"role": "Captain", "ship": "HMS Sheffield", "class": "Type 42"},
  "ship": {"cell": "AU20", "heading": 270, "speed": 15},
  "world": {"year": 1982, "theater": "San Carlos Water", "side": "Royal Navy", "adversary": "Argentina", "ship": "HMS Sheffield", "ship_class": "Type 42", "carrier": "HMS Hermes"},
  "radar": {
    "locked_id": 4,
    "top_threat_id": 4,
    "contacts": [
      {"id": 4, "name": "A-4 Skyhawk", "type": "Hostile", "cell": "AA00", "range_nm": 24.8, "course": 315, "speed": 289},
      {"id": 3, "name": "Sea Harrier FRS.1", "type": "Friendly", "cell": "AJ26", "range_nm": 27.7, "course": 212, "speed": 315}
    ]
  },
  "weapons": [
    {"name": "MM38 Exocet", "class": "Missile", "ammo": 2, "armed": "Safe", "min_nm": 8, "max_nm": 22, "in_range": false, "cooldown_s": 0},
    {"name": "Sea Dart SAM", "class": "SAM", "ammo": 16, "armed": "Armed", "min_nm": 2, "max_nm": 35, "in_range": true, "cooldown_s": 0}
  ],
  "cap": {
    "ready_pairs": 1,
    "airframes": 4,
    "cooldown_s": 0,
    "missions": [ {"id": 12, "status": "patrol"} ]
  },
  "capabilities": ["RADAR.SCAN", "RADAR.LOCK", "WEAPON.ARM", "WEAPON.FIRE", "CAP.LAUNCH", "CAP.AUTHORIZE"],
  "policy": {"always_confirm": ["WEAPON.FIRE", "CAP.AUTHORIZE", "CAP.LAUNCH"]}
}
```

Additions for initiative and memory
- history.recent_radio: last 3–5 lines {role,text}
- history.recent_events: last 3–5 structured events {id,text,data}
- doctrine:
  - standing_orders: brief behavioral guardrails (proactive but safe)
  - recommendations: thresholds (e.g., arm SAM if hostile within 25 nm)
- style: { tone, role_styles, style_seed }
- variety_policy: { avoid_repeats_window_s }

Response Schema (LLM must return only this JSON)
```
{
  "radio": "string",                     // in-character RN clipped radio, ≤ 2 short lines
  "actions": [                            // zero or more executable actions (strictly from capabilities)
    {"type": "RADAR.SCAN"},
    {"type": "RADAR.LOCK", "params": {"id": 4}},
    {"type": "WEAPON.ARM",  "params": {"name": "Sea Dart SAM", "state": "Armed"}},
    {"type": "WEAPON.FIRE", "params": {"weapon": "Sea Dart SAM", "target_id": 4, "mode": "real"}},
    {"type": "CAP.LAUNCH", "params": {"cell": "AF05"}},
    {"type": "CAP.AUTHORIZE", "params": {"mission_id": 12, "authorize": true}}
  ],
  "advisories": [                         // optional suggestions (never auto-executed)
    {
      "advice": "Bring Sea Dart online.",
      "reason": "Nearest hostile within 25 nm and closing.",
      "recommended_action": {"type": "WEAPON.ARM", "params": {"name": "Sea Dart SAM", "state": "Armed"}},
      "priority": "info|warning|critical",
      "ttl_s": 30
    }
  ],
  "needs_confirm": true,                  // true if any proposed action would change state
  "clarifying_question": "string|null"   // concise, if confirmation or disambiguation needed
}
```

System Prompt (for JSON/fn-calling)
```
You are the Operations/Radio Officer aboard HMS Sheffield (1982 Royal Navy).
- Interpret the Captain’s words and the provided context_pack.
- Reply in clipped RN radio discipline. Address the CO as “Captain”.
- Keep radio ≤ 2 short lines. Include bearings/ranges if relevant.
Constraints:
- Return ONLY valid JSON matching the schema. Do not include prose outside JSON.
- Do not invent contacts, weapons, or missions absent from context_pack.
- Prefer actions that match capabilities; if missing, ask a concise clarifying question.
- Offer brief recommendations when safety or mission success warrants it; place them in "advisories" and (optionally) echo succinctly in radio.
- If the action is dangerous or irreversible, set needs_confirm=true and ask to confirm.
- If asked for data, answer using context_pack; if unknown, say so briefly.
```

Action Definitions (tools)
- RADAR.SCAN → performs immediate scan (no confirmation).
- RADAR.LOCK {id} → set current primary lock to a known contact id.
- WEAPON.ARM {name,state} → set arming state; may be suggested via advisories; execution may or may not require confirmation by policy.
- WEAPON.FIRE {weapon, target_id, mode} → requires ARMED, ammo>0, in_range; always confirm.
- CAP.LAUNCH {cell} → launches a pair to grid cell; confirm.
- CAP.AUTHORIZE {mission_id, authorize} → grants/denies engagement; confirm.

Validation Matrix (server-side)
- RADAR.SCAN: allowed always.
- RADAR.LOCK: id must exist in contacts; lock becomes priority_id.
- WEAPON.ARM:
  - name in catalog; state in {Armed, Safe}.
  - Executes via POST /weapons/arm; confirmation policy configurable (default: no confirm).
- WEAPON.FIRE:
  - name in catalog; ammo[name] > 0; arming[name] == 'Armed'.
  - primary lock exists and equals target_id; in_range(name, primary) == True.
  - always confirm before POST /weapons/fire.
- CAP.LAUNCH:
  - CAP available; ready_pairs > 0; not on cooldown; cell valid AA00..AN39.
  - confirm before POST /cap/launch_to.
- CAP.AUTHORIZE:
  - mission_id exists; confirm required before POST /cap/authorize.

Piper Voices (server)
- Radar: en_GB-alan-medium
- Fire Control: en_GB-vctk-medium (male speaker id)
- Weapons: en_GB-jenny_dioco-medium
- Engineering: en_GB-alba-medium

Observability
- Log ai.request/ai.response (redacted), actions.validated/applied, radio.start/end, tts.input/output/cache_hit.
- Debug UI: live queue and last interpreter JSON.

Phases
1) Interpreter endpoint that returns JSON only (no execution).
2) Add validation + confirmation loop; then wire to existing routes.
3) Server-side audio prep with Piper + PTT FX baked-in; remove client radio filter.
4) Expand phrasebook tone and CAP dialogue flows.
