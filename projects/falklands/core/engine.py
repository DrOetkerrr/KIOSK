# projects/falklands/core/engine.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import json
import time

# Subsystems that already exist in your tree
from projects.falklands.systems.nav import NavSystem

# Your tree has radar_live.py (not radar.py). We alias it to RadarSystem here.
try:
    from projects.falklands.systems.radar_live import RadarLive as RadarSystem
except Exception:
    RadarSystem = None  # type: ignore

# State container
from .state import FalklandsState


@dataclass
class Engine:
    """
    Minimal orchestrator:
      - owns state (FalklandsState)
      - registers Nav + Radar (if present)
      - ticks subsystems (call tick() regularly)
      - routes /nav and /radar slash commands
      - produces a one-line HUD string
    """
    state_path: Path
    autosave: bool = True

    st: FalklandsState = field(init=False)
    systems: Dict[str, Any] = field(init=False)

    _nav: Optional[NavSystem] = field(default=None, init=False)
    _radar: Optional[Any] = field(default=None, init=False)  # RadarSystem or None

    _last_hud: str = field(default="", init=False)
    _last_tick: float = field(default_factory=time.time, init=False)

    def __post_init__(self) -> None:
        # Load or create state JSON
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
            except Exception:
                data = {}
        else:
            data = {}
        self.st = FalklandsState(data=data)

        # Initialize systems defaults (radar online by default)
        systems = self.st.data.setdefault("systems", {})
        radar_state = systems.get("radar")
        if not isinstance(radar_state, dict):
            systems["radar"] = {"online": True, "degraded": False}
        else:
            radar_state.setdefault("online", True)
            radar_state.setdefault("degraded", False)
        self.systems = systems

        # Register subsystems
        self._register_systems()

        # Initial HUD + persist
        self._last_hud = self.hud_line()
        self._save_if_enabled()

    # ---------- subsystem registration ----------
    def _register_systems(self) -> None:
        self._nav = NavSystem(self.st)

        if RadarSystem is not None:
            try:
                self._radar = RadarSystem(self.st)
            except Exception:
                self._radar = None
        else:
            self._radar = None

    # ---------- ticking ----------
    def tick(self, dt: float) -> None:
        """Advance live systems; call from your runner’s loop."""
        if self._nav:
            try:
                self._nav.tick(dt)
            except Exception:
                pass

        if self._radar:
            try:
                # radar_live expects tick(dt) too
                self._radar.tick(dt)
            except Exception:
                pass

        self._last_hud = self.hud_line()
        self._save_if_enabled()

    # ---------- HUD ----------
    def hud_line(self) -> str:
        d = self.st.data
        ship = d.get("ship", {})
        col = ship.get("col", 50)
        row = ship.get("row", 50)
        hdg = ship.get("heading", 270)
        spd = ship.get("speed", 15)

        primary_txt = "No active contact"
        if self._radar:
            try:
                p = self._radar.primary()
                if p:
                    clock = p.get("clock", "?")
                    rng = p.get("range_nm", "?")
                    grid = p.get("grid", "?")
                    ctype = p.get("type", "Contact")
                    primary_txt = f"Primary {ctype} at {clock}, {rng} NM, grid {grid}"
            except Exception:
                pass

        return f"Ship {col}-{row} | hdg {hdg}° spd {spd} kn; {primary_txt}"

    # ---------- public state ----------
    def public_state(self) -> Dict[str, Any]:
        """A safe snapshot for the LLM (keep it small)."""
        d = self.st.data
        ship = d.get("ship", {})
        out = {
            "ship": {
                "col": ship.get("col", 50),
                "row": ship.get("row", 50),
                "heading": ship.get("heading", 270),
                "speed": ship.get("speed", 15),
            },
            "CELL_NM": d.get("CELL_NM", 4.0),
        }
        # include primary if exists
        if self._radar:
            try:
                p = self._radar.primary()
                if p:
                    out["primary"] = {
                        "id": p.get("id"),
                        "type": p.get("type"),
                        "grid": p.get("grid"),
                        "range_nm": p.get("range_nm"),
                        "clock": p.get("clock"),
                        "threat": p.get("threat", "unknown"),
                    }
            except Exception:
                pass
        return out

    # ---------- command routing ----------
    def exec_slash(self, line: str) -> str:
        line = (line or "").strip()
        if not line.startswith("/"):
            return "ERR: not a slash command"

        try:
            topic, *rest = line[1:].split(None, 1)
        except Exception:
            return "ERR: malformed command"

        args = (rest[0] if rest else "").strip()

        if topic == "nav":
            return self._cmd_nav(args)
        if topic == "radar":
            return self._cmd_radar(args)
        if topic == "status":
            return self.hud_line()

        return f"ERR: unknown command '{topic} {args}'."

    # ---------- persistence ----------
    def save(self) -> None:
        """Persist current state to JSON at state_path."""
        try:
            # Ensure directory exists
            if self.state_path:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps(self.st.data, indent=2) + "\n", encoding="utf-8")
        except Exception:
            # Non-fatal: persistence should not crash the engine
            pass

    def _save_if_enabled(self) -> None:
        """Call save() only when autosave is enabled and state_path is set."""
        try:
            if bool(getattr(self, "autosave", False)) and getattr(self, "state_path", None):
                self.save()
        except Exception:
            # Never raise from autosave guard
            pass

    # ---------- helpers ----------
    def _parse_kv(self, s: str) -> Dict[str, str]:
        kv: Dict[str, str] = {}
        for part in s.split():
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k.strip()] = v.strip().strip('"')
        return kv

    # /nav ...
    def _cmd_nav(self, args: str) -> str:
        if not self._nav:
            return "ERR: nav system unavailable"

        if args == "" or args.startswith("show"):
            return self._nav.show({})

        if args.startswith("set"):
            kv = self._parse_kv(args)
            payload: Dict[str, Any] = {}
            if "heading" in kv:
                try:
                    payload["heading"] = float(kv["heading"])
                except Exception:
                    pass
            if "speed" in kv:
                try:
                    payload["speed"] = float(kv["speed"])
                except Exception:
                    pass
            return self._nav.set(payload)

        return "ERR: unknown /nav command"

    # /radar ...
    def _cmd_radar(self, args: str) -> str:
        # Respect explicit offline state only
        rs = self.systems.get("radar", {})
        if not bool(rs.get("online", True)):
            return "ERR: radar offline"

        if args.startswith("scan"):
            return self.radar_scan()

        if args.startswith("list"):
            try:
                if self._radar and hasattr(self._radar, "list_contacts"):
                    items = self._radar.list_contacts()
                    return f"RADAR: {len(items)} contact(s)"
                contacts = self.st.data.get("contacts", {})
                n = len(contacts) if isinstance(contacts, dict) else 0
                return f"RADAR: {n} contact(s)"
            except Exception:
                return "RADAR: list unavailable"

        if args.startswith("primary"):
            parts = args.split()
            if len(parts) >= 2:
                tid = parts[1]
                try:
                    if self._radar and hasattr(self._radar, "set_primary"):
                        return self._radar.set_primary(tid)
                except Exception:
                    pass
                return "RADAR: set_primary unavailable"
            return "ERR: /radar primary <id>"

        return "ERR: unknown /radar command"

    # Headless radar scan wrapper
    def _headless_radar_scan(self) -> str:
        contacts = self.st.data.get("contacts", {})
        n = len(contacts) if isinstance(contacts, dict) else 0
        return f"RADAR: scanned, {n} contact(s)"

    def radar_scan(self) -> str:
        """Perform a radar sweep even in headless mode; never 'unavailable'."""
        rs = self.systems.get("radar", {})
        if not bool(rs.get("online", True)):
            return "ERR: radar offline"

        if self._radar and hasattr(self._radar, "scan"):
            try:
                out = self._radar.scan()
            except Exception:
                out = None

            n = None
            if self._radar and hasattr(self._radar, "list_contacts"):
                try:
                    n = len(self._radar.list_contacts())
                except Exception:
                    n = None

            if isinstance(out, str) and out.strip():
                s = out.strip()
                if s.lower().startswith("radar:"):
                    return s
                if n is not None:
                    return f"RADAR: scanned, {n} contact(s)"
                return "RADAR: scanned"

            if n is not None:
                return f"RADAR: scanned, {n} contact(s)"
            return "RADAR: scanned"

        # No hardware radar bound: headless path
        return self._headless_radar_scan()
