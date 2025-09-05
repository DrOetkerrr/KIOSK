#!/usr/bin/env python3
"""
FalklandV2 — Step 1: Real-time clock + scheduler (no hardware)

Purpose
- Establish a robust real-time simulation backbone:
  * Monotonic clock driving sim time at a configurable speed (default 1×).
  * Pause/resume without time drift.
  * Small event scheduler: schedule events in future sim-seconds, trigger on time.
  * Minimal HUD + CLI to observe and test.

Usage
  Run, then try:
    status
    schedule 5 spawn TestContact
    pause
    resume
    speed 2
    tick 10
    reset
    help
    exit
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, List
import sys
import shlex
import threading
import time
import heapq

GridPos = Tuple[str, int]  # reserved for later

# ---------------- Core Game State ----------------

@dataclass
class GameState:
    sim_time_s: float = 0.0           # simulation time in seconds
    timescale: float = 1.0            # 1.0 = realtime
    running: bool = True              # paused/resumed

    # World placeholders (we’ll expand later)
    contacts_count: int = 0
    mode: str = "SIM"

    # Bookkeeping
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def timecode(self) -> str:
        hours = int(self.sim_time_s // 3600)
        return f"H+{hours}"

    def hud_line(self) -> str:
        return f"{self.timecode()} | Contacts {self.contacts_count} | Mode {self.mode} | x{self.timescale:.2f}"

    def reset_world(self) -> None:
        self.sim_time_s = 0.0
        self.timescale = 1.0
        self.running = True
        self.contacts_count = 0
        self.mode = "SIM"


# ---------------- Event Scheduler ----------------

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
        due: List[ScheduledEvent] = []
        with self._lock:
            while self._pq and self._pq[0].due_time_s <= now_s:
                due.append(heapq.heappop(self._pq))
        return due

    def clear(self) -> None:
        with self._lock:
            self._pq.clear()


# ---------------- Clock Loop (background thread) ----------------

class GameLoop:
    def __init__(self, state: GameState, scheduler: Scheduler, print_fn=print):
        self.state = state
        self.scheduler = scheduler
        self.print = print_fn

        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="GameLoop", daemon=True)

        self._last_wall = time.monotonic()
        self._hud_next_s = 0.0
        self._cadence_s = 0.1

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        self._thread.join(timeout=2.0)

    def pause(self):
        self.state.running = False

    def resume(self):
        self._last_wall = time.monotonic()
        self.state.running = True

    def set_speed(self, scale: float):
        self.state.timescale = max(0.0, scale)

    def jump(self, delta_sim_s: float):
        self.state.sim_time_s += max(0.0, delta_sim_s)
        self._trigger_due_events()

    def reset(self):
        self.state.reset_world()
        self.scheduler.clear()
        self._hud_next_s = 0.0
        self._last_wall = time.monotonic()

    # --- internal ---

    def _run(self):
        while not self._stop_evt.is_set():
            now_wall = time.monotonic()
            dt_wall = now_wall - self._last_wall
            self._last_wall = now_wall

            if self.state.running and self.state.timescale > 0.0:
                self.state.sim_time_s += dt_wall * self.state.timescale
                self._trigger_due_events()
                self._heartbeat()

            time.sleep(self._cadence_s)

    def _trigger_due_events(self):
        for ev in self.scheduler.pop_due(self.state.sim_time_s):
            if ev.label.lower().startswith("spawn"):
                self.state.contacts_count += 1
                self.print(f"[{self.state.timecode()}] EVENT: {ev.label} (contacts={self.state.contacts_count})")
            else:
                self.print(f"[{self.state.timecode()}] EVENT: {ev.label}")

    def _heartbeat(self):
        if self.state.sim_time_s >= self._hud_next_s:
            self.print(self.state.hud_line())
            self._hud_next_s = int(self.state.sim_time_s) + 1.0


# ---------------- CLI ----------------

PROMPT = "FalklandV2> "

HELP_TEXT = """Commands:
  status                 Show HUD now
  schedule <sec> <label> [payload]   Schedule an event in <sec> (sim-seconds)
  pause                  Pause the real-time clock
  resume                 Resume the real-time clock
  speed [factor]         Show or set time scale (1 = realtime)
  tick [sec]             Manually advance sim time by N seconds (default 1)
  reset                  Reset world and clear events
  help                   This help
  exit / quit            Leave the program
"""

def parse_float(s: Optional[str], default: float) -> float:
    if s is None:
        return default
    try:
        return float(s)
    except ValueError:
        raise ValueError("Expected a number")

def main(argv: list[str]) -> int:
    state = GameState()
    sched = Scheduler()
    loop = GameLoop(state, sched)
    loop.start()

    print("FalklandV2 realtime backbone ready (no hardware). Type 'help' for commands.")
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

            cmd = parts[0].lower()
            args = parts[1:]

            try:
                if cmd in ("exit", "quit"):
                    break
                elif cmd == "help":
                    print(HELP_TEXT, end="")
                elif cmd == "status":
                    print(state.hud_line())
                elif cmd == "schedule":
                    if len(args) < 2:
                        print("Usage: schedule <sec> <label> [payload]")
                        continue
                    delay_s = parse_float(args[0], default=0.0)
                    label = args[1]
                    payload = " ".join(args[2:]) if len(args) > 2 else None
                    sched.schedule_in(delay_s, label, payload, now_s=state.sim_time_s)
                    print(f"Scheduled '{label}' in {delay_s:.1f}s (at t={state.sim_time_s + delay_s:.1f})")
                elif cmd == "pause":
                    loop.pause()
                    print("Paused.")
                elif cmd == "resume":
                    loop.resume()
                    print("Resumed.")
                elif cmd == "speed":
                    if len(args) == 0:
                        print(f"Speed x{state.timescale:.2f}")
                    else:
                        scale = parse_float(args[0], default=1.0)
                        loop.set_speed(scale)
                        print(f"Speed set to x{state.timescale:.2f}")
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