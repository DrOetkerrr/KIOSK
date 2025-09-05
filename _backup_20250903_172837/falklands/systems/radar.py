from __future__ import annotations
from typing import Dict, List
from ..core.state import FalklandsState
import random

CONTACT_TYPES = ["aircraft", "missile", "surface", "helicopter", "unknown"]

class RadarSystem:
    """
    Minimal radar:
      /radar add bearing=040 range=22 type=aircraft
      /radar show
      /radar scan range=5         # probabilistic new contact within range (NM)
    """
    def __init__(self, st: FalklandsState):
        self.st = st
        self.st.data.setdefault("radar", {}).setdefault("contacts", [])

    # ---- helpers ----
    @property
    def _contacts(self) -> List[dict]:
        return self.st.data["radar"]["contacts"]

    # ---- commands ----
    def add_contact(self, args: Dict[str, str]) -> str:
        brg = args.get("bearing", "?")
        rng = args.get("range", "?")
        typ = args.get("type", "unknown")
        contact = {"bearing": brg, "range": rng, "type": typ}
        self._contacts.append(contact)
        return f"Radar: contact added bearing {brg} range {rng} type {typ}"

    def show(self, args: Dict[str, str]) -> str:
        if not self._contacts:
            return "Radar: no contacts."
        lines = [
            f"#{i+1} brg {c.get('bearing','?')} rng {c.get('range','?')} type {c.get('type','?')}"
            for i, c in enumerate(self._contacts)
        ]
        return "Radar contacts:\n" + "\n".join(lines)

    def scan(self, args: Dict[str, str]) -> str:
        """
        Perform a simple sweep. With modest probability, spawn ONE contact
        inside the requested range (NM). If none spawned, report 'no new contacts'.
        Usage: /radar scan range=5
               /sensors scan range=10   (alias via engine)
        """
        try:
            max_rng = float(args.get("range", "5"))
        except ValueError:
            return "Radar: invalid range (NM)."

        # 30% chance to spawn a single new contact
        spawned = False
        if random.random() < 0.3:
            bearing = f"{random.randint(0,359):03d}"
            # keep at least 1 NM, clamp to max_rng
            rng_val = max(1.0, round(random.uniform(1.0, max(1.0, max_rng)), 1))
            ctype = random.choice(CONTACT_TYPES)
            self._contacts.append({"bearing": bearing, "range": rng_val, "type": ctype})
            spawned = True

        if spawned:
            last = self._contacts[-1]
            return f"Radar: sweep complete — NEW contact brg {last['bearing']} rng {last['range']} NM type {last['type']}."
        else:
            return "Radar: sweep complete — no new contacts."