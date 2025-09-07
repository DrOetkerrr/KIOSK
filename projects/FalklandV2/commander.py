#!/usr/bin/env python3
"""
Falklands V2 — Commander shim (Step 12)
Adds: `weapons status` (reads data/ship.json and prints ammo + ranges).

Existing features kept:
  radar status | radar scan | radar lock #ID | radar unlock
  nav course <deg> | nav speed <kts> | nav stop | nav come <deg> [kts] | nav goto <CELL>
  quiet on|off | pause | resume | help | exit
"""

from __future__ import annotations
import sys, time, re, select, contextlib, json
from pathlib import Path

# Local imports
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import Engine
from subsystems import radar as rdar
from subsystems import nav as navi
from subsystems import weapons as weap

DATA = ROOT / "data"

LOCK_RE       = re.compile(r"^\s*radar\s+lock\s+#?(\d+)\s*$", re.IGNORECASE)
COURSE_RE     = re.compile(r"^\s*nav\s+course\s+(-?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
SPEED_RE      = re.compile(r"^\s*nav\s+speed\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
COME_RE       = re.compile(r"^\s*nav\s+come\s+(-?\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?\s*$", re.IGNORECASE)
GOTO_RE       = re.compile(r"^\s*nav\s+goto\s+([A-Za-z])\s*([1-2]?[0-9]|26)\s*$", re.IGNORECASE)

HELP_TEXT = """\
Commands:
  radar status         - show radar picture (cell → type → distance)
  radar scan           - perform an immediate scan now
  radar lock #ID       - lock a contact by id (e.g., radar lock #5)
  radar unlock         - clear current lock

  weapons status       - show own-ship weapons (ammo + ranges)

  nav course <deg>     - set course (0–359.9, 0=N, 90=E)
  nav speed <kts>      - set speed (>=0 kts)
  nav stop             - set speed to 0
  nav come <deg> [kts] - set both at once, e.g., 'nav come 270 18'
  nav goto <CELL>      - teleport (test), e.g., 'nav goto N13'

  pause                - pause ticking
  resume               - resume ticking
  quiet on|off         - mute/unmute engine prints during ticks (default: on)
  help | ?             - this help
  quit | exit          - stop
"""

@contextlib.contextmanager
def _mute_stdout(enabled: bool):
    if not enabled:
        yield
        return
    class _Sink:
        def write(self, *args, **kwargs): pass
        def flush(self): pass
    old = sys.stdout
    try:
        sys.stdout = _Sink()
        yield
    finally:
        sys.stdout = old

def _print_status(eng: Engine) -> None:
    sx, sy = eng._ship_xy()
    locked = eng.state.get("radar", {}).get("locked_contact_id")
    line = rdar.status_line(eng.pool, (sx, sy), locked_id=locked, max_list=3)
    print(line)

def _lock(eng: Engine, cid: int) -> None:
    if not any(c.id == cid for c in eng.pool.contacts):
        print(f"RADAR: contact #{cid} not found.")
        return
    rdar.lock_contact(eng.state, cid)
    print(f"RADAR: locked contact #{cid}.")
    _print_status(eng)

def _unlock(eng: Engine) -> None:
    if eng.state.get("radar", {}).get("locked_contact_id") is None:
        print("RADAR: no target locked.")
        return
    rdar.unlock_contact(eng.state)
    print("RADAR: lock cleared.")
    _print_status(eng)

def _scan_now(eng: Engine) -> None:
    eng._radar_scan()  # engine prints its own status

def _helm_course(eng: Engine, deg: float) -> None:
    ship = eng.state.setdefault("ship", {})
    deg = float(deg) % 360.0
    ship["course_deg"] = deg
    eng._autosave()
    print(f"HELM: course set to {deg:.1f}°")
    print("HUD:", eng.hud())

def _helm_speed(eng: Engine, kts: float) -> None:
    ship = eng.state.setdefault("ship", {})
    kts = max(0.0, float(kts))
    ship["speed_kts"] = kts
    eng._autosave()
    print(f"HELM: speed set to {kts:.1f} kts")
    print("HUD:", eng.hud())

def _helm_stop(eng: Engine) -> None:
    _helm_speed(eng, 0.0)

def _helm_come(eng: Engine, deg: float, kts: float | None) -> None:
    _helm_course(eng, deg)
    if kts is not None:
        _helm_speed(eng, kts)

def _helm_goto(eng: Engine, cell_str: str) -> None:
    try:
        x, y = navi.parse_cell(cell_str)
    except Exception as e:
        print(f"HELM: bad cell '{cell_str}': {e}")
        return
    ship = eng.state.setdefault("ship", {})
    ship["pos"] = {"x": float(x), "y": float(y)}
    ship["cell"] = cell_str.upper().replace(" ", "")
    eng._autosave()
    print(f"HELM: jumped to {ship['cell']}")
    print("HUD:", eng.hud())

def _weapons_status() -> None:
    path = DATA / "ship.json"
    if not path.exists():
        print("WEAPONS: no ship.json found in data/.")
        return
    try:
        ship = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WEAPONS: failed to read ship.json: {e}")
        return
    name = ship.get("name", "Own Ship")
    klass = ship.get("class", "")
    head = f"{name} ({klass})" if klass else name
    print(head)
    print(weap.weapons_status(ship))

def _handle(line: str, eng: Engine, flags: dict) -> bool:
    s = line.strip()
    s_l = s.lower()
    if not s:
        return True
    if s_l in ("quit", "exit"):
        return False
    if s_l in ("help", "?"):
        print(HELP_TEXT); return True

    # Radar commands
    if s_l == "radar status":
        _print_status(eng); return True
    if s_l == "radar unlock":
        _unlock(eng); return True
    if s_l == "radar scan":
        _scan_now(eng); return True
    m = LOCK_RE.match(s)
    if m:
        _lock(eng, int(m.group(1))); return True

    # Weapons
    if s_l == "weapons status":
        _weapons_status(); return True

    # Helm commands
    m = COURSE_RE.match(s)
    if m:
        _helm_course(eng, float(m.group(1))); return True
    m = SPEED_RE.match(s)
    if m:
        _helm_speed(eng, float(m.group(1))); return True
    if s_l == "nav stop":
        _helm_stop(eng); return True
    m = COME_RE.match(s)
    if m:
        deg = float(m.group(1))
        kts = float(m.group(2)) if m.group(2) is not None else None
        _helm_come(eng, deg, kts); return True
    m = GOTO_RE.match(s)
    if m:
        cell = f"{m.group(1).upper()}{m.group(2)}"
        _helm_goto(eng, cell); return True

    # Console controls
    if s_l == "pause":
        flags["paused"] = True; print("Paused. (engine ticking is stopped)"); return True
    if s_l == "resume":
        flags["paused"] = False; print("Resumed."); return True
    if s_l.startswith("quiet "):
        arg = s_l.split(None, 1)[1].lower()
        if arg in ("on","off"):
            flags["quiet"] = (arg == "on")
            print(f"Quiet mode {'ON' if flags['quiet'] else 'OFF'}.")
            return True

    print("Unrecognized command. Type 'help' for options.")
    return True

def main() -> None:
    eng = Engine()
    print("Commander online. Type 'help' for commands.")
    print("HUD:", eng.hud())

    tick = float(eng.game_cfg.get("tick_seconds", 1.0))
    flags = {"paused": False, "quiet": True}

    print("> ", end="", flush=True)
    running = True
    while running:
        rlist, _, _ = select.select([sys.stdin], [], [], tick)
        if rlist:
            line = sys.stdin.readline()
            if not line:
                break
            running = _handle(line, eng, flags)
            if running:
                print("> ", end="", flush=True)
        else:
            if flags["paused"]:
                continue
            with _mute_stdout(flags["quiet"]):
                eng.tick(tick)
            if not flags["quiet"]:
                print("> ", end="", flush=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCommander offline.")