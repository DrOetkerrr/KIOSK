#!/usr/bin/env python3
"""
Falklands V2 — entry point (bootstrap).
"""

from __future__ import annotations
import json, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE = ROOT / "state"

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.write("\n")

def bootstrap_configs() -> None:
    # Minimal example — just enough to run
    _write_json(DATA / "game.json", {
        "grid": {"cols": 26, "rows": 26, "cell_nm": 1.0},
        "start": {"ship_cell": "K13", "course_deg": 0.0, "speed_kts": 0.0},
        "tick_seconds": 1.0
    })
    _write_json(STATE / "runtime.json", {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "ship": {"cell": "K13", "course_deg": 0.0, "speed_kts": 0.0},
        "contacts": []
    })

def load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def hud_line(game_cfg, state):
    ship = state["ship"]
    return (f"POS={ship['cell']} "
            f"COG={ship['course_deg']:.1f}° "
            f"SOG={ship['speed_kts']:.1f} kts | "
            f"{len(state.get('contacts', []))} contacts")

def main():
    bootstrap_configs()
    game_cfg = load(DATA / "game.json")
    state = load(STATE / "runtime.json")
    print("HUD:", hud_line(game_cfg, state))
    for i in range(5):
        time.sleep(game_cfg["tick_seconds"])
        print(f"[t+{i+1}s] {hud_line(game_cfg, state)}")

if __name__ == "__main__":
    main()