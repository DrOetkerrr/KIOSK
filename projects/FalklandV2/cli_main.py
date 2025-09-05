#!/usr/bin/env python3
"""
FalklandV2 — Step 2 (quiet HUD): realtime clock + scheduler + grid navigation

New: HUD auto-printing is OFF by default to avoid clobbering input.
Use:  hud off   -> disable heartbeat
      hud 1     -> heartbeat every 1 sim-second (or any N seconds)
      status    -> manual HUD print

Try:
  status
  grid
  nav
  setpos K12
  course 270
  speed 15
  hud 2
  pause
  resume
  hud off
  reset
  exit
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, List
import math, sys, shlex, threading, time, heapq, re

# ---------- Grid constants ----------
GRID_COLS = 26              # A..Z
GRID_ROWS = 26              # 1..26
CELL_NM   = 2.0             # nautical miles per cell

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

GridPos = Tuple[str, int]  # e.g., ("K", 12)

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

    # World placeholders
    contacts_count: int = 0
    mode: str = "SIM"

    # Bookkeeping
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def current_cell_indices(self) -> Tuple[int, int]:
        xi = int(clamp(math.floor(self.x_cells + 1e-9), 0, GRID_COLS - 1))
        yi = int(clamp(math.floor(self.y_cells + 1e-9), 0, GRID_ROWS - 1))
        return xi, yi

    def grid_pos(self) -> GridPos:
        xi, yi = self.current_cell_indices()
        return (idx_to_col(xi), yi + 1)

    def timecode(self) -> str:
        return f"H+{int(self.sim_time_s // 3600)}"

    def hud_line(self) -> str:
        col, row = self.grid_pos()
        return f"{self.timecode()} | Pos {col}{row} | Spd {self.speed_kn:.1f} kn | Crs {self.course_deg:.0f}° | Contacts {self.contacts_count} | Mode {self.mode} | x{self.timescale:.2f}"

    def reset_world(self) -> None:
        self.sim_time_s = 0.0
        self.timescale = 1.0
        self.running = True
        self.x_cells = float(col_to_idx("K"))
        self.y_cells = 11.0
        self.speed_kn = 0.0
        self.course_deg = 0.0
        self.contacts_count = 0
        self.mode = "SIM"

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
        # schedule next print from "now" to feel responsive
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
            self.print(f"[{self.state.timecode()}] NAV: Edge reached at {cell_name(*self.state.current_cell_indices())}. Speed set to 0.")

    def _trigger_due_events(self):
        for ev in self.scheduler.pop_due(self.state.sim_time_s):
            if ev.label.lower().startswith("spawn"):
                self.state.contacts_count += 1
                self.print(f"[{self.state.timecode()}] EVENT: {ev.label} (contacts={self.state.contacts_count})")
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
  schedule <sec> <label> [payload]   Schedule event in <sec> (sim-seconds)
  pause | resume               Control realtime clock
  timescale [factor]           Show or set simulation speed (1 = realtime)
  tick [sec]                   Manually advance sim time by N seconds (default 1)
  reset                        Reset world and clear events
  help                         This help
  exit | quit                  Leave the program
"""

def parse_float(s: Optional[str], default: float) -> float:
    if s is None: return default
    try: return float(s)
    except ValueError: raise ValueError("Expected a number")

def main(argv: list[str]) -> int:
    state = GameState()
    sched = Scheduler()
    loop = GameLoop(state, sched)
    loop.start()

    print("FalklandV2 realtime + navigation (quiet HUD). Type 'help' for commands.")
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
                        loop.set_hud_interval(0.0)
                        print("HUD heartbeat off.")
                    else:
                        sec = max(0.1, parse_float(args[0], default=1.0))
                        loop.set_hud_interval(sec)
                        print(f"HUD heartbeat every {sec:.1f}s.")

                elif cmd == "nav":
                    col,row = state.grid_pos()
                    print(f"Cell {col}{row} | x={state.x_cells:.3f} y={state.y_cells:.3f} | Spd {state.speed_kn:.2f} kn | Crs {state.course_deg:.1f}°")

                elif cmd == "grid":
                    print(f"Grid A–Z x 1–26, cell size {CELL_NM} NM")

                elif cmd == "setpos":
                    if not args:
                        print("Usage: setpos <Cell>")
                        continue
                    x_idx, y_idx = parse_cell(args[0])
                    state.x_cells = float(x_idx)
                    state.y_cells = float(y_idx)
                    print(f"Position set to {cell_name(*state.current_cell_indices())}")

                elif cmd == "course":
                    if not args:
                        print(f"Course {state.course_deg:.1f}°")
                    else:
                        deg = parse_float(args[0], default=state.course_deg)
                        state.course_deg = deg % 360.0
                        print(f"Course set to {state.course_deg:.1f}°")

                elif cmd == "speed":
                    if not args:
                        print(f"Speed {state.speed_kn:.2f} kn")
                    else:
                        kn = max(0.0, parse_float(args[0], default=state.speed_kn))
                        state.speed_kn = kn
                        print(f"Speed set to {state.speed_kn:.2f} kn")

                elif cmd == "stop":
                    state.speed_kn = 0.0
                    print("Speed set to 0 kn")

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
                    if not args:
                        print(f"Timescale x{state.timescale:.2f}")
                    else:
                        scale = parse_float(args[0], default=1.0)
                        loop.set_timescale(scale)
                        print(f"Timescale set to x{state.timescale:.2f}")

                elif cmd == "tick":
                    delta = parse_float(args[0] if args else None, default=1.0)
                    loop.jump(delta)
                    print(state.hud_line())

                elif cmd == "reset":
                    loop.reset()
                    print("Reset OK.")
                    print(state.hud_line())

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