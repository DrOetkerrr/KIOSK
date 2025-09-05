# falklands/core/state.py
from __future__ import annotations
import json
from pathlib import Path

class GameState:
    """
    Persistent game state (numeric grid).
    Columns 1..MAP_COLS (west→east), rows 1..MAP_ROWS (north→south).
    """
    MAP_COLS = 100
    MAP_ROWS = 100
    CELL_NM  = 4.0  # each cell ≈ 4 NM

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {}
        self._ensure_defaults()
        self.load()

    def _ensure_defaults(self):
        if not self.data:
            self.data = {
                "contacts": {},              # id -> contact dict
                "primary_id": None,
                "engagement_pending": False,
                "awaiting_kill_confirm": False,

                # Start centered on the map
                "ship_position": {"col_f": 50.0, "row_f": 50.0},
                "ship_course_deg": 270.0,    # west
                "ship_speed_kn": 15.0,

                "CELL_NM": self.CELL_NM,
                "MAX_SPEED": 32.0,
                "current_hour": 0,
                "ammo": {},

                # Map bounds (for convenience)
                "MAP_COLS": self.MAP_COLS,
                "MAP_ROWS": self.MAP_ROWS,
            }

    def load(self):
        if self.path.exists():
            try:
                obj = json.loads(self.path.read_text())
                if isinstance(obj, dict):
                    self.data.update(obj)
            except Exception:
                # Ignore corrupt/partial save
                pass

    def save(self):
        try:
            self.path.write_text(json.dumps(self.data, indent=2))
        except Exception:
            pass

# ---- Backward compatibility (so engine.py can still import FalklandsState) ----
FalklandsState = GameState