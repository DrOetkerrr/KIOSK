#!/usr/bin/env python3
# reset_weapons.py — one-time migration to set Falklands loadout to Sea Cat

from pathlib import Path
import json, sys

STATE = Path.home() / "kiosk" / "state_falklands.json"

def main():
    if not STATE.exists():
        print(f"State file not found: {STATE}")
        sys.exit(1)

    obj = json.loads(STATE.read_text(encoding="utf-8"))
    data = obj.setdefault("data", {})
    w = data.setdefault("weapons", {})

    # Corrected Falklands loadout
    w["inventory"] = ["Sea Cat", "20 mm cannon"]
    if w.get("selected") not in w["inventory"]:
        w["selected"] = None
    w.setdefault("safe", True)

    STATE.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Weapons loadout reset to:", w["inventory"], "selected:", w["selected"], "safe:", w["safe"])

if __name__ == "__main__":
    main()