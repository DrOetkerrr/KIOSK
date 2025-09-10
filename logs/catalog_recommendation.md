# Catalog Discovery Report

This report scans for potential unit catalogs (enemies/friendlies/weapons).

## Canonical Schema

```json
{
  "name": "...",
  "allegiance": "Hostile|Friendly",
  "speed_kts": 0,
  "weight": 0,
  "class": "Aircraft|Helicopter|Ship|...",
  "weapons": [
    {
      "name": "...",
      "qty": 0
    }
  ]
}
```

## Candidates

- `falklands_state.json` (json, 90 bytes) — matches: radar
- `projects/falklandV2/state/runtime.json` (json, 2434 bytes) — matches: aircraft, friendly, hostile, radar, ship
- `projects/falklandV2/state/ammo.json` (json, 64 bytes) — matches: weapon, weapons
- `projects/falklandV2/state/arming.json` (json, 66 bytes) — matches: weapon, weapons
- `projects/falklandV2/rules/falklands_rules.json` (json, 4926 bytes) — matches: aircraft, radar
- `projects/falklandV2/.vscode/tasks.json` (json, 1121 bytes) — matches: radar
- `projects/falklandV2/characters/ensign.json` (json, 866 bytes) — matches: enemy, weapon, weapons
- `projects/falklandV2/data/game.json` (json, 234 bytes) — matches: radar, ship
- `projects/falklandV2/data/convoy.json` (json, 740 bytes) — matches: friendly, ship, unit, units
- `projects/falklandV2/data/cap_config.json` (json, 1243 bytes) — matches: aircraft, weapon, weapons
- `projects/falklandV2/data/ship.json` (json, 2007 bytes) — matches: aircraft, helicopter, radar, ship, weapon, weapons
- `projects/falklandV2/data/audio_config.json` (json, 687 bytes) — matches: weapon
- `projects/falklandV2/data/contacts.json` (json, 3403 bytes) — matches: aircraft, friendly, helicopter, hostile, ship, weapon
  - sample entries:
    - {"name": "A-4 Skyhawk", "allegiance": "Hostile", "speed_kts": 385, "weight": 5, "class": "Aircraft"}
    - {"name": "Dagger (Mirage V)", "allegiance": "Hostile", "speed_kts": 420, "weight": 4, "class": "Aircraft"}
    - {"name": "Mirage III", "allegiance": "Hostile", "speed_kts": 455, "weight": 3, "class": "Aircraft"}
- `projects/falklands/state.json` (json, 18757 bytes) — matches: aircraft, friendly, inventory, radar, ship, unit, weapon, weapons
- `_backup_20250903_172837/falklands/state.json` (json, 18757 bytes) — matches: aircraft, friendly, inventory, radar, ship, unit, weapon, weapons
- `_backup_20250903_172837/falklands/data/weapons_db.json` (json, 1206 bytes) — matches: loadout, ship
  - sample entries:
    - {"name": "4.5-inch gun"}
    - {"name": "20 mm cannon"}
    - {"name": "Seacat SAM"}

## Recommendations

- Prefer `projects/falklandV2/data/contacts.json` as primary unit catalog: it already contains name, allegiance, speed_kts, weight, and type→class.

- Consider `_backup_.../falklands/data/weapons_db.json` for weapons ranges and typical loadouts; map name→weapon name and typical_loadout→qty.

- Keep radar HOSTILES table in sync or replace with entries from contacts.json where allegiance==Hostile and type=="Aircraft".

