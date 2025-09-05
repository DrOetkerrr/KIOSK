# falklands/systems/engineering.py
from __future__ import annotations

class EngineeringSystem:
    def __init__(self, st):
        self.st = st
        d = self.st.data
        d.setdefault("engineering", {"malfunctions": [], "propulsion_ok": True})

    def status(self, args):
        e = self.st.data["engineering"]
        state = "Nominal" if e.get("propulsion_ok", True) and not e.get("malfunctions") else "Degraded"
        return f"ENGINEERING: {state}. Malfunctions: {', '.join(e['malfunctions']) if e['malfunctions'] else 'none'}."

    def report(self, args):
        # Same as status for now; later we can add richer detail
        return self.status(args)

    def repair(self, args):
        sys_name = args.get("system", "engines")
        e = self.st.data["engineering"]
        if sys_name in e.get("malfunctions", []):
            e["malfunctions"].remove(sys_name)
            return f"ENGINEERING: {sys_name} repaired."
        return f"ENGINEERING: no fault found in {sys_name}."