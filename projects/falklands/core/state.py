# projects/falklands/core/state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any

DEFAULT_STATE: Dict[str, Any] = {
    "map": {"cols": 100, "rows": 100, "CELL_NM": 4.0},
    "ship": {"col": 50, "row": 50, "heading": 270.0, "speed": 15.0, "max_speed": 32.0},
    "contacts": {},           # id -> contact dict
    "primary_id": None,       # id of primary tracked contact
    "weapons": {"armed": False, "selected": None, "inventory": {}},
    "clock": {"t": 0.0},      # seconds since start (game time)
}

@dataclass
class FalklandsState:
    """Lightweight state container with a dict inside."""
    data: Dict[str, Any] = field(default_factory=lambda: DEFAULT_STATE.copy())

    def ensure_defaults(self) -> None:
        # Merge any missing top-level keys
        for k, v in DEFAULT_STATE.items():
            if k not in self.data:
                # shallow copy is fine for our usage
                self.data[k] = v if not isinstance(v, dict) else v.copy()

def public_state(st: FalklandsState) -> Dict[str, Any]:
    """
    A trimmed snapshot safe to show the LLM.
    (Hides inventories if we ever want; currently OK to expose.)
    """
    d = {}
    s = st.data
    d["map"] = s.get("map", {})
    d["ship"] = s.get("ship", {}).copy()
    d["primary"] = None
    pid = s.get("primary_id")
    if pid:
        d["primary"] = s.get("contacts", {}).get(pid)
    d["contacts_count"] = len(s.get("contacts", {}))
    return d