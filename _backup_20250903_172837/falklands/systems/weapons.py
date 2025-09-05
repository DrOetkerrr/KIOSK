from __future__ import annotations
from typing import Dict
from ..core.state import FalklandsState

class WeaponsSystem:
    """
    Basic weapons control.
    Commands:
      /weapons show
      /weapons arm
      /weapons safe
      /weapons select name="Sea Cat"
    """
    def __init__(self, st: FalklandsState):
        self.st = st
        # Ensure defaults exist
        w = self.st.data.setdefault("weapons", {})
        w.setdefault("safe", True)
        w.setdefault("selected", None)
        # Correct Falklands loadout
        w.setdefault("inventory", ["Sea Cat", "20 mm cannon"])

    def show(self, args: Dict[str, str]) -> str:
        w = self.st.data["weapons"]
        inv = ", ".join(w.get("inventory", []))
        return f"Weapons: safe={w.get('safe', True)} selected={w.get('selected')} inventory=[{inv}]"

    def arm(self, args: Dict[str, str]) -> str:
        self.st.data["weapons"]["safe"] = False
        return "Weapons: ARMED"

    def safe(self, args: Dict[str, str]) -> str:
        self.st.data["weapons"]["safe"] = True
        return "Weapons: SAFE"

    def select(self, args: Dict[str, str]) -> str:
        name = args.get("name")
        if not name:
            return "Weapons: need name=... (e.g., /weapons select name=\"Sea Cat\")"
        inv = self.st.data["weapons"].get("inventory", [])
        if name not in inv:
            return f"Weapons: '{name}' not in inventory {inv}"
        self.st.data["weapons"]["selected"] = name
        return f"Weapons: selected {name}"