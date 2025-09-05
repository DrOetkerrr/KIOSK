#!/usr/bin/env python3
import json
from pathlib import Path

DB = Path.home() / "kiosk" / "falklands" / "data" / "weapons_db.json"

def main():
    data = json.loads(DB.read_text(encoding="utf-8"))
    print(f"Loaded {len(data)} weapon entries from {DB}:\n")
    for w in data:
        print(f"- {w['name']}: range {w['min_nm']}-{w['max_nm']} NM, "
              f"Pkill(in/wrong/out)={w['p_kill_in']}/{w['p_kill_wrong']}/{w['p_kill_out']}, "
              f"typical_loadout={w['typical_loadout']}")
    # quick sanity checks
    names = {w["name"] for w in data}
    must_have = {"20 mm cannon", "Seacat SAM"}
    missing = must_have - names
    if missing:
        print("\nERROR: missing expected entries:", missing)
    else:
        print("\nSanity OK: Seacat + 20mm present")

if __name__ == "__main__":
    main()