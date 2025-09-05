#!/usr/bin/env python3
"""
FalklandV2 — Step 3: realtime clock + scheduler + grid nav + contacts (cap 15) + priority logic

New in this step
- Deterministic RNG via `seed <int>`.
- Contacts pool (max 15) with id/label/side/threat and exact cell coords.
- Spawns appear at >=10 NM (default exact 10.0 NM ring) from own ship.
- `spawn [Label] [Side]` and scheduler-driven `schedule N spawn [Label] [Side]`.
- `contacts` lists all; `setside <id> <side>`; `setthreat <id> <0-10>`; `clrcontacts` clears.
- `priority` shows the current priority target (highest threat, then nearest).

Try:
  status
  seed 42
  setpos K12
  spawn Bogey ENEMY
  spawn Trader NEUTRAL
  contacts
  priority
  schedule 5 spawn Skunk ENEMY
  hud 2
  hud off
  setthreat 2 8
  setside 2 ENEMY
  priority
  clrcontacts
  exit
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, List
import math, sys, shlex, threading, time, heapq, re, random

# ---------- Grid constants ----------
GRID_COLS = 26              # A..Z
GRID_ROWS = 26              # 1..26
CELL_NM   = 2.0             # nautical miles per cell
SPAWN_MIN_NM = 10.0         # minimum spawn range from own ship
SPAWN_RING_NM = 10.0        # default ring distance (exact for now)

def col_to_idx(col: str) -> int:
    c = col.strip().upper()
    if len(c) != 1 or not ('A' <= c <= 'Z'):
        raise ValueError("Column must be A..Z")
    return ord(c) - ord('A')

def idx_to_col(idx: int) -> str:
    if not (0 <= idx < GRID_COLS):
        raise ValueError("Column index out of bounds")
    return chr(ord('A') + idx)

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def parse_cell(token: str) -> Tuple[int, int]:
    m = re.fullmatch(r'([A-Za-z])\s*([1-9]|1\d|2[0-6])', token.strip())
    if not m:
        raise ValueError("Cell must look like K12 (A–Z then 1–26)")
    x = col_to_idx(m.group(1))
    y = int(m.group(2)) - 1
    return (x, y)

def cell_name(x_idx: int, y_idx: int) -> str:
    return f"{idx_to_col(x_idx)}{y_idx + 1}"

# ---------- Bearing/Range helpers ----------
def nm_from_cells(dx_cells: float, dy_cells: float) -> float:
    return math.hypot(dx_cells, dy_cells) * CELL_NM

def bearing_T_deg(dx_cells: float, dy_cells: float) -> float:
    """
    True bearing: 0°=North, clockwise positive.
    Our grid has x increasing east, y increasing south.
    Vector from own->target in NM: dx_nm = dx_cells*CELL_NM, dy_nm = -dy_cells*CELL_NM (north positive).
    """
    dx_nm = dx_cells * CELL_NM
    dy_nm = -dy_cells * CELL_NM
    ang = math.degrees(math.atan2(dx_nm, dy_nm))  # atan2(East, North)
    if ang < 0:
        ang += 360.0
    return ang

# ---------- Core game state ----------
@dataclass
class GameState:
    # Simulation clock
    sim_time_s: float = 0.0
    timescale: float = 1.0
    running: bool = True

    # Navigation (continuous cell coords; (0,0)=A1 top-left)
    x_cells: float = float(col_to_idx("K"))   # start near K12
    y_cells: float = 11.0
    speed_kn: float = 0.0                     # knots (nm/h)
    course_deg: float = 0.0                   # 0=N, 90=E, 180=S, 270=W

    # World — contacts
    next_contact_id: int = 1
    contacts: List["Contact"] = field(default_factory=list)
    contacts_cap: int = 15

    # Misc
    mode: str = "SIM"
    rng: random.Random = field(default_factory=random.Random)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # --- HUD/time ---
    def grid_pos(self) -> Tuple[str, int]:
        xi = int(clamp(math.floor(self.x_cells + 1e-9), 0, GRID_COLS - 1))
        yi = int(clamp(math.floor(self.y_cells + 1e-9), 0, GRID_ROWS - 1))
        return (idx_to_col(xi), yi + 1)

    def timecode(self) -> str:
        return f"H+{int(self.sim_time_s // 3600)}"

    def hud_line(self) -> str:
        col, row = self.grid_pos()
        return f"{self.timecode()} | Pos {col}{row} | Spd {self.speed_kn:.1f} kn | Crs {self.course_deg:.0f}° | Contacts {len(self.contacts):d} | Mode {self.mode} | x{self.timescale:.2f}"

    def reset_world(self) -> None:
        self.sim_time_s = 0.0
        self.timescale = 1.0
        self.running = True
        self.x_cells = float(col_to_idx("K"))
        self.y_cells = 11.0
        self.speed_kn = 0.0
        self.course_deg = 0.0
        self.next_contact_id = 1
        self.contacts.clear()
        self.mode = "SIM"

    # --- Contacts API ---
    def spawn_contact(self, label: str = "Contact", side: str = "ENEMY") -> "Contact":
        if len(self.contacts) >= self.contacts_cap:
            raise RuntimeError(f"Contact cap reached ({self.contacts_cap}). Use 'clrcontacts' or remove some.")
        # Choose a random bearing, place at ring SPAWN_RING_NM, convert to cell offsets.
        brg = self.rng.uniform(0.0, 360.0)
        # Convert polar (range, bearing) to cell deltas. North=0 so:
        dy_nm = math.cos(math.radians(brg)) * SPAWN_RING_NM  # north+
        dx_nm = math.sin(math.radians(brg)) * SPAWN_RING_NM  # east+
        dx_cells = dx_nm / CELL_NM
        dy_cells = dy_nm / CELL_NM

        cx = clamp(self.x_cells + dx_cells, 0.0, GRID_COLS - 1e-6)
        cy = clamp(self.y_cells - dy_cells, 0.0, GRID_ROWS - 1e-6)  # north reduces y

        c = Contact(
            cid=self.next_contact_id,
            label=label,
            side=normalize_side(side),
            threat=default_threat_for_side(side),
            x_cells=cx,
            y_cells=cy,
        )
        self.next_contact_id += 1
        self.contacts.append(c)
        return c

    def list_contacts(self) -> List["Contact"]:
        return list(self.contacts)

    def clear_contacts(self) -> None:
        self.contacts.clear()

    def find_contact(self, cid: int) -> Optional["Contact"]:
        for c in self.contacts:
            if c.cid == cid:
                return c
        return None

    def contact_range_bearing(self, c: "Contact") -> Tuple[float, float]:
        dx = c.x_cells - self.x_cells
        dy = c.y_cells - self.y_cells
        rng_nm = nm_from_cells(dx, dy)
        brg = bearing_T_deg(dx, dy)
        return rng_nm, brg

    def priority_contact(self) -> Optional["Contact"]:
        """Pick contact with highest threat; tie-break by nearest range."""
        if not self.contacts:
            return None
        # Determine max threat
        max_thr = max(c.threat for c in self.contacts)
        candidates = [c for c in self.contacts if c.threat == max_thr]
        # Among those, choose nearest
        best = min(candidates, key=lambda c: self.contact_range_bearing(c)[0])
        return best

@dataclass
class Contact:
    cid: int
    label: str
    side: str            # 'NEUTRAL' | 'FRIENDLY' | 'ENEMY'
    threat: int          # 0..10
    x_cells: float
    y_cells: float

    def cell_name(self) -> str:
        xi = int(clamp(math.floor(self.x_cells + 1e-9), 0, GRID_COLS - 1))
        yi = int(clamp(math.floor(self.y_cells + 1e-9), 0, GRID_ROWS - 1))
        return f"{idx_to_col(xi)}{yi+1}"

def normalize_side(side: str) -> str:
    s = (side or "ENEMY").strip().upper()
    if s not in ("NEUTRAL", "FRIENDLY", "ENEMY"):
        s = "ENEMY"
    return s

def default_threat_for_side(side: str) -> int:
    s = normalize_side(side)
    if s == "ENEMY":
        return 5
    if s == "FRIENDLY":
        return 0
    return 1  # NEUTRAL default

# ---------- Event scheduler ----------
@dataclass(order=True)
class ScheduledEvent:
    due_time_s: float
    label: str = field(compare=False)
    payload: Optional[str] = field(default=None, compare=False)

class Scheduler:
    def __init__(self):
        self._pq: List[ScheduledEvent] = []
        self._lock = threading.Lock()

    def schedule_in(self, delay_s: float, label: str, payload: Optional[str] = None, now_s: float = 0.0) -> None:
        ev = ScheduledEvent(due_time_s=now_s + max(0.0, delay_s), label=label, payload=payload)
        with self._lock:
            heapq.heappush(self._pq, ev)

    def pop_due(self, now_s: float) -> List[ScheduledEvent]:
        out: List[ScheduledEvent] = []
        with self._lock:
            while self._pq and self._pq[0].due_time_s <= now_s:
                out.append(heapq.heappop(self._pq))
        return out

    def clear(self) -> None:
        with self._lock:
            self._pq.clear()

# ---------- Game loop ----------
class GameLoop:
    def __init__(self, state: GameState, scheduler: Scheduler, print_fn=print):
        self.state = state
        self.scheduler = scheduler
        self.print = print_fn
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="GameLoop", daemon=True)
        self._last_wall = time.monotonic()
        self._last_sim_s = self.state.sim_time_s
        # HUD heartbeat control: 0 = off (manual only). Set via 'hud N'.
        self._hud_interval_s = 0.0
        self._hud_next_s = 0.0
        self._cadence_s = 0.1

    def start(self): self._thread.start()
    def stop(self):
        self._stop_evt.set()
        self._thread.join(timeout=2.0)

    def pause(self): self.state.running = False
    def resume(self):
        self._last_wall = time.monotonic()
        self._last_sim_s = self.state.sim_time_s
        self.state.running = True

    def set_timescale(self, scale: float): self.state.timescale = max(0.0, scale)

    def set_hud_interval(self, seconds: float):
        self._hud_interval_s = max(0.0, seconds)
        if self._hud_interval_s > 0.0:
            self._hud_next_s = self.state.sim_time_s + self._hud_interval_s

    def jump(self, delta_sim_s: float):
        ds = max(0.0, delta_sim_s)
        self.state.sim_time_s += ds
        self._integrate_nav(ds)
        self._trigger_due_events()
        self._maybe_heartbeat()

    def reset(self):
        self.state.reset_world()
        self.scheduler.clear()
        self._hud_next_s = 0.0
        self._last_wall = time.monotonic()
        self._last_sim_s = self.state.sim_time_s

    # --- internals ---
    def _run(self):
        while not self._stop_evt.is_set():
            now_wall = time.monotonic()
            dt_wall = now_wall - self._last_wall
            self._last_wall = now_wall

            if self.state.running and self.state.timescale > 0.0:
                self.state.sim_time_s += dt_wall * self.state.timescale
                dt_sim = self.state.sim_time_s - self._last_sim_s
                if dt_sim > 0:
                    self._integrate_nav(dt_sim)
                    self._trigger_due_events()
                    self._maybe_heartbeat()
                    self._last_sim_s = self.state.sim_time_s
            time.sleep(self._cadence_s)

    def _integrate_nav(self, dt_sim_s: float):
        if self.state.speed_kn <= 0.0 or dt_sim_s <= 0.0:
            return
        dist_nm = self.state.speed_kn * (dt_sim_s / 3600.0)
        ang = math.radians(self.state.course_deg % 360.0)
        dy_nm = math.cos(ang) * dist_nm    # +north
        dx_nm = math.sin(ang) * dist_nm    # +east
        dx_cells = dx_nm / CELL_NM
        dy_cells = dy_nm / CELL_NM
        new_x = self.state.x_cells + dx_cells
        new_y = self.state.y_cells - dy_cells  # north reduces y

        clamped_x = clamp(new_x, 0.0, GRID_COLS - 1e-6)
        clamped_y = clamp(new_y, 0.0, GRID_ROWS - 1e-6)
        hit_edge = (abs(clamped_x - new_x) > 1e-9) or (abs(clamped_y - new_y) > 1e-9)

        self.state.x_cells = clamped_x
        self.state.y_cells = clamped_y

        if hit_edge and self.state.speed_kn > 0.0:
            self.state.speed_kn = 0.0
            self.print(f"[{self.state.timecode()}] NAV: Edge reached at {cell_name(int(clamped_x), int(clamped_y))}. Speed set to 0.")

    def _trigger_due_events(self):
        for ev in self.scheduler.pop_due(self.state.sim_time_s):
            parts = (ev.payload or "").split() if ev.payload else []
            # allow payload like "Skunk ENEMY"
            side = parts[-1] if parts and parts[-1].upper() in ("NEUTRAL","FRIENDLY","ENEMY") else "ENEMY"
            label = " ".join(parts[:-1]) if side and parts and parts[-1].upper() in ("NEUTRAL","FRIENDLY","ENEMY") else (ev.payload or ev.label)
            if ev.label.lower().startswith("spawn"):
                try:
                    c = self.state.spawn_contact(label=label or "Contact", side=side)
                    self.print(f"[{self.state.timecode()}] EVENT: spawn -> #{c.cid} {c.label} {c.side} at {c.cell_name()}")
                except Exception as e:
                    self.print(f"[{self.state.timecode()}] EVENT: spawn failed: {e}")
            else:
                self.print(f"[{self.state.timecode()}] EVENT: {ev.label}")

    def _maybe_heartbeat(self):
        if self._hud_interval_s > 0.0 and self.state.sim_time_s >= self._hud_next_s:
            self.print(self.state.hud_line())
            self._hud_next_s = self.state.sim_time_s + self._hud_interval_s

# ---------- CLI ----------
PROMPT = "FalklandV2> "
HELP_TEXT = """Commands:
  status                       Show HUD now
  hud off | <seconds>          Control auto-HUD printing (default off)
  nav                          Show nav details (cell + subcell coords, speed/course)
  setpos <Cell>                Set position to e.g. K12
  course <deg>                 Set course (0=N, 90=E, 180=S, 270=W)
  speed [kn]                   Show or set speed in knots
  stop                         Set speed to 0
  grid                         Show grid/cell configuration

  seed <int>                   Set RNG seed for deterministic spawns
  spawn [Label] [Side]         Spawn at 10 NM ring (Side optional: ENEMY|NEUTRAL|FRIENDLY)
  schedule <sec> spawn [Label] [Side]
  contacts                     List contacts with id, label, side, cell, range NM, bearing °T, threat
  clrcontacts                  Remove all contacts
  setside <id> <Side>          Change side for a contact
  setthreat <id> <0-10>        Set threat level (0..10)
  priority                     Show current priority target (highest threat, then nearest)

  pause | resume               Control realtime clock
  timescale [factor]           Show or set simulation speed (1 = realtime)
  tick [sec]                   Manually advance sim time by N seconds (default 1)
  reset                        Reset world and clear events/contacts
  help                         This help
  exit | quit                  Leave the program
"""

def parse_float(s: Optional[str], default: float) -> float:
    if s is None: return default
    try: return float(s)
    except ValueError: raise ValueError("Expected a number")

def parse_int(s: Optional[str], default: int) -> int:
    if s is None: return default
    try: return int(s)
    except ValueError: raise ValueError("Expected an integer")

def main(argv: list[str]) -> int:
    state = GameState()
    sched = Scheduler()
    loop = GameLoop(state, sched)
    loop.start()

    print("FalklandV2 realtime + nav + contacts (quiet HUD). Type 'help' for commands.")
    try:
        while True:
            try:
                line = input(PROMPT)
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line.strip():
                continue
            try:
                parts = shlex.split(line)
            except ValueError as e:
                print(f"! parse error: {e}")
                continue

            cmd, *args = (parts[0].lower(), *parts[1:])

            try:
                if cmd in ("exit","quit"):
                    break

                elif cmd == "help":
                    print(HELP_TEXT, end="")

                elif cmd == "status":
                    print(state.hud_line())

                elif cmd == "hud":
                    if not args:
                        print("Usage: hud off | <seconds>")
                    elif args[0].lower() == "off":
                        loop.set_hud_interval(0.0); print("HUD heartbeat off.")
                    else:
                        sec = max(0.1, parse_float(args[0], default=1.0))
                        loop.set_hud_interval(sec); print(f"HUD heartbeat every {sec:.1f}s.")

                elif cmd == "nav":
                    col,row = state.grid_pos()
                    print(f"Cell {col}{row} | x={state.x_cells:.3f} y={state.y_cells:.3f} | Spd {state.speed_kn:.2f} kn | Crs {state.course_deg:.1f}°")

                elif cmd == "grid":
                    print(f"Grid A–Z x 1–26, cell size {CELL_NM} NM")

                elif cmd == "setpos":
                    if not args:
                        print("Usage: setpos <Cell>"); continue
                    x_idx, y_idx = parse_cell(args[0])
                    state.x_cells = float(x_idx)
                    state.y_cells = float(y_idx)
                    print(f"Position set to {cell_name(x_idx,y_idx)}")

                elif cmd == "course":
                    if not args: print(f"Course {state.course_deg:.1f}°")
                    else:
                        deg = parse_float(args[0], default=state.course_deg)
                        state.course_deg = deg % 360.0; print(f"Course set to {state.course_deg:.1f}°")

                elif cmd == "speed":
                    if not args: print(f"Speed {state.speed_kn:.2f} kn")
                    else:
                        kn = max(0.0, parse_float(args[0], default=state.speed_kn))
                        state.speed_kn = kn; print(f"Speed set to {state.speed_kn:.2f} kn")

                elif cmd == "stop":
                    state.speed_kn = 0.0; print("Speed set to 0 kn")

                # --- RNG / Contacts ---
                elif cmd == "seed":
                    if not args: print("Usage: seed <int>")
                    else:
                        state.rng.seed(parse_int(args[0], 0)); print("Seed set.")

                elif cmd == "spawn":
                    label = args[0] if args else "Contact"
                    side = args[1] if len(args) > 1 else "ENEMY"
                    try:
                        c = state.spawn_contact(label=label, side=side)
                        print(f"Spawned #{c.cid} {c.label} {c.side} at {c.cell_name()} (thr {c.threat})")
                    except Exception as e:
                        print(f"! spawn failed: {e}")

                elif cmd == "contacts":
                    if not state.contacts:
                        print("(no contacts)")
                    else:
                        for c in state.contacts:
                            rng_nm, brg = state.contact_range_bearing(c)
                            print(f"#{c.cid:02d} {c.label:12s} {c.side:9s} {c.cell_name():>3s}  rng {rng_nm:5.1f} NM  brg {brg:6.1f}°T  thr {c.threat}")

                elif cmd == "clrcontacts":
                    state.clear_contacts(); print("Contacts cleared.")

                elif cmd == "setside":
                    if len(args) < 2: print("Usage: setside <id> <NEUTRAL|FRIENDLY|ENEMY>")
                    else:
                        cid = parse_int(args[0], 0)
                        side = normalize_side(args[1])
                        c = state.find_contact(cid)
                        if not c: print(f"! no contact with id {cid}")
                        else:
                            c.side = side
                            # Optional: adjust default threat when changing side (only if unchanged)
                            print(f"#{c.cid} side set to {c.side}")

                elif cmd == "setthreat":
                    if len(args) < 2: print("Usage: setthreat <id> <0-10>")
                    else:
                        cid = parse_int(args[0], 0)
                        thr = int(clamp(parse_int(args[1], 0), 0, 10))
                        c = state.find_contact(cid)
                        if not c: print(f"! no contact with id {cid}")
                        else:
                            c.threat = thr; print(f"#{c.cid} threat set to {c.threat}")

                elif cmd == "priority":
                    pc = state.priority_contact()
                    if not pc:
                        print("(no priority target)")
                    else:
                        rng_nm, brg = state.contact_range_bearing(pc)
                        print(f"PRIORITY -> #{pc.cid} {pc.label} {pc.side} at {pc.cell_name()} | rng {rng_nm:.1f} NM | brg {brg:.1f}°T | thr {pc.threat}")

                # --- Scheduler ---
                elif cmd == "schedule":
                    if len(args) < 2:
                        print("Usage: schedule <sec> <label> [payload]")
                        continue
                    delay_s = parse_float(args[0], default=0.0)
                    label = args[1]
                    payload = " ".join(args[2:]) if len(args) > 2 else None
                    sched.schedule_in(delay_s, label, payload, now_s=state.sim_time_s)
                    due = state.sim_time_s + delay_s
                    print(f"Scheduled '{label}' in {delay_s:.1f}s (t={due:.1f})")

                elif cmd == "pause":
                    loop.pause(); print("Paused.")
                elif cmd == "resume":
                    loop.resume(); print("Resumed.")

                elif cmd == "timescale":
                    if not args: print(f"Timescale x{state.timescale:.2f}")
                    else:
                        scale = parse_float(args[0], default=1.0)
                        loop.set_timescale(scale); print(f"Timescale set to x{state.timescale:.2f}")

                elif cmd == "tick":
                    delta = parse_float(args[0] if args else None, default=1.0)
                    loop.jump(delta); print(state.hud_line())

                elif cmd == "reset":
                    loop.reset(); print("Reset OK."); print(state.hud_line())

                else:
                    print(f"! unknown command: {cmd}. Type 'help'.")

            except Exception as e:
                print(f"! error: {e}")

    finally:
        loop.stop()
        print("Goodbye.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))