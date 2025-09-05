from __future__ import annotations
from typing import Dict
from ..core.state import FalklandsState

class TargetsSystem:
    """
    Manages known target list.
    Commands:
      /targets add name="HMS Example" type=frigate status=hostile
      /targets list
    """
    def __init__(self, st: FalklandsState):
        self.st = st

    def add(self, args: Dict[str, str]) -> str:
        name = args.get("name", f"T{len(self.st.data['targets'])+1}")
        typ = args.get("type", "unknown")
        status = args.get("status", "unknown")
        tgt = {"name": name, "type": typ, "status": status}
        self.st.data["targets"].append(tgt)
        return f"Target added: {name} ({typ}, {status})"

    def list(self, args: Dict[str, str]) -> str:
        tgts = self.st.data["targets"]
        if not tgts:
            return "No targets known."
        lines = [
            f"#{i+1} {t['name']} ({t['type']}, {t['status']})"
            for i, t in enumerate(tgts)
        ]
        return "Targets:\n" + "\n".join(lines)