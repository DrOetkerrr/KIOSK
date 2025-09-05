# falklands/core/engine.py
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Any
import math, time, threading

from .state import GameState, FalklandsState  # alias kept
from ..systems.nav import NavSystem
from ..systems.weapons import WeaponsSystem
from ..systems.radar_live import RadarLive
try:
    from .router import Router
except Exception:
    Router = None

def _cap(v, lo, hi): return max(lo, min(hi, v))

class Engine:
    """
    Wires subsystems, runs real-time ticker, and exposes:
      - ask(text) -> reply string (includes [Executed] section)
      - pop_alert() -> next alert string or None
    """
    def __init__(self, state_path):
        # normalize state
        if hasattr(state_path, "data"):
            self.st = state_path
        else:
            p = Path(state_path)
            self.st = FalklandsState(p)

        # ensure some defaults we rely on
        d = self.st.data
        d.setdefault("ammo", {"Sea Cat": 22, "20 mm cannon": 2000})
        d.setdefault("weapons_armed", False)
        d.setdefault("ship_speed_kn", 15.0)
        d.setdefault("ship_course_deg", 270.0)
        d.setdefault("ship_position", {"col_f": 50.0, "row_f": 50.0})
        d.setdefault("MAP_COLS", 100); d.setdefault("MAP_ROWS", 100)
        d.setdefault("CELL_NM", 4.0)

        # subsystems
        self.nav = NavSystem(self.st)
        self.weapons = WeaponsSystem(self.st)
        self.radar_live = RadarLive(self.st)

        self._router = Router(self) if Router else None
        self._alerts: List[str] = []
        self._stop = False

        # ticker thread
        def _ticker():
            last = time.time()
            while not self._stop:
                now = time.time()
                dt = _cap(now - last, 0.1, 1.0)
                last = now
                # move ship
                try: self.nav.step(dt)
                except Exception: pass
                # radar update
                try:
                    self.radar_live.step(dt)
                    for msg in self.radar_live.check_alerts():
                        self._alerts.append(msg)
                except Exception: pass
                # periodic save
                try: self.st.save()
                except Exception: pass
                time.sleep(0.2)
        self._thr = threading.Thread(target=_ticker, daemon=True)
        self._thr.start()

    # --------- public API ----------
    def pop_alert(self) -> str | None:
        return self._alerts.pop(0) if self._alerts else None

    def stop(self):
        self._stop = True
        try: self._thr.join(timeout=1.0)
        except Exception: pass
        try: self.st.save()
        except Exception: pass

    def ask(self, text: str) -> str:
        """
        Use LLM router (if available) to produce a short radio reply + optional slash actions.
        Execute those actions and append an [Executed] section with results.
        """
        spoken, actions = ("Aye, Captain.", [])
        # route
        if self._router:
            try:
                spoken, actions = self._router.handle(text)
            except Exception:
                pass
        # fallback mini-heuristics
        if not actions:
            low = text.lower()
            if "status" in low:
                actions = ["/status report"]
            elif "arm" in low and "weapon" in low:
                actions = ["/weapons arm"]
            elif "course" in low or "heading" in low:
                actions = ["/nav show"]

        # execute actions
        executed_lines: List[str] = []
        for cmd in actions:
            res = self._exec_command(cmd)
            executed_lines.append(res)

        # build reply
        out = spoken.strip()
        if executed_lines:
            out += "\n\n[Executed]\n" + "\n".join(executed_lines)
        return out

    # --------- command execution ----------
    def _exec_command(self, cmd: str) -> str:
        """
        Execute a single slash command and return a status line.
        """
        c = cmd.strip()
        if not c.startswith("/"):
            return f"IGNORED: {c}"
        try:
            if c.startswith("/status report"):
                return self._do_status_report()
            if c.startswith("/nav"):
                return self._do_nav(c)
            if c.startswith("/radar"):
                return self._do_radar(c)
            if c.startswith("/weapons"):
                return self._do_weapons(c)
            return f"ERR: unknown command '{c[1:]}'"
        except Exception as e:
            return f"ERR: {c} -> {e}"

    # ---- helpers per command group ----
    def _do_status_report(self) -> str:
        d = self.st.data
        cols = int(d.get("MAP_COLS", 100)); rows = int(d.get("MAP_ROWS", 100))
        pos = d.get("ship_position", {}); c = int(round(pos.get("col_f", 50.0))); r = int(round(pos.get("row_f", 50.0)))
        hdg = float(d.get("ship_course_deg", 270.0)); spd = float(d.get("ship_speed_kn", 15.0))
        return f"NAV: grid {c}-{r:02d} hdg {hdg:.0f} spd {spd:.0f} kn"

    def _do_nav(self, c: str) -> str:
        d = self.st.data
        toks = c.split()
        if len(toks) >= 3 and toks[1] == "set":
            # parse key=value pairs
            kv = {k:v for k,v in (t.split("=",1) for t in toks[2:] if "=" in t)}
            if "heading" in kv:
                try:
                    hdg = int(kv["heading"]) % 360
                    d["ship_course_deg"] = hdg
                except: pass
            if "speed" in kv:
                try:
                    spd = float(kv["speed"])
                    spd = _cap(spd, 0.0, float(d.get("MAX_SPEED", 32.0)))
                    d["ship_speed_kn"] = spd
                except: pass
            return self._do_status_report()
        if len(toks) >= 2 and toks[1] == "show":
            return self._do_status_report()
        return "ERR: NAV usage"

    def _do_radar(self, c: str) -> str:
        d = self.st.data
        toks = c.split()
        if len(toks) >= 2 and toks[1] == "list":
            # list detected contacts
            cons = [x for x in d.get("contacts", {}).values() if x.get("_detected")]
            if not cons:
                return "RADAR: no detected contacts"
            cons.sort(key=lambda x: x.get("range_nm", 9e9))
            lines = []
            for x in cons[:6]:
                nm = x.get("name","contact")
                clk = x.get("clock","?")
                rng = x.get("range_nm","?")
                grid = x.get("grid","??-??")
                lines.append(f"RADAR: {nm} at {clk} o'clock, {rng:.1f} NM, grid {grid}")
            return "\n".join(lines)
        if len(toks) >= 2 and toks[1] == "primary":
            # auto = closest detected
            cons = [x for x in d.get("contacts", {}).values() if x.get("_detected")]
            if not cons:
                d["primary_id"] = None
                return "RADAR: no detected contacts; primary cleared"
            sel = min(cons, key=lambda x: x.get("range_nm", 9e9))
            d["primary_id"] = sel["id"]
            return f"RADAR: primary set to {sel.get('name','contact')} ({sel['id']})"
        return "ERR: RADAR usage"

    def _do_weapons(self, c: str) -> str:
        d = self.st.data
        toks = c.split()
        if len(toks) >= 2 and toks[1] == "arm":
            d["weapons_armed"] = True
            return "WEAPONS: armed and online"
        if len(toks) >= 2 and toks[1] == "safe":
            d["weapons_armed"] = False
            d["selected_weapon"] = None
            return "WEAPONS: SAFE"
        if len(toks) >= 2 and toks[1] == "inventory":
            ammo = d.get("ammo", {})
            if not ammo: return "WEAPONS: no inventory"
            return "WEAPONS: " + ", ".join(f"{k}={v}" for k,v in ammo.items())
        if len(toks) >= 2 and toks[1] == "select":
            # expect name="<weapon>"
            try:
                m = c.split("name=",1)[1].strip()
                if m.startswith('"') and m.endswith('"'):
                    m = m[1:-1]
                if m in d.get("ammo", {}):
                    d["selected_weapon"] = m
                    return f"WEAPONS: selected {m}"
                else:
                    inv = list(d.get("ammo", {}).keys())
                    return f"WEAPONS: '{m}' not in inventory {inv}"
            except Exception:
                return "ERR: select usage"
        if len(toks) >= 2 and toks[1] == "fire":
            if not d.get("weapons_armed", False):
                return "WEAPONS: cannot fire (SAFE)"
            w = d.get("selected_weapon")
            if not w:
                return "WEAPONS: no weapon selected"
            ammo = d.get("ammo", {})
            if ammo.get(w, 0) <= 0:
                return f"WEAPONS: '{w}' out of ammo"
            pid = d.get("primary_id")
            cons = d.get("contacts", {})
            tgt = cons.get(pid) if pid in cons else None
            if not tgt or not tgt.get("_detected"):
                return "WEAPONS: no detected primary target"
            # super simple outcome for now
            ammo[w] = max(0, ammo[w]-1)
            d["ammo"] = ammo
            # crude range check: inside 12 NM for guns; inside 5 NM for Sea Cat
            rng = float(tgt.get("range_nm", 1e9))
            ok = True
            if w == "Sea Cat":
                ok = (rng <= 5.0)
            elif w == "20 mm cannon":
                ok = (rng <= 2.0)
            outcome = "Target destroyed." if ok else "Target missed."
            return f"WEAPONS: fired {w} at {rng:.1f} NM — {outcome}"
        return "ERR: WEAPONS usage"