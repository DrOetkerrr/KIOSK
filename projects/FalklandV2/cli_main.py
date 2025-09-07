#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FalklandV2 CLI — full file replacement

Goals
- Solid REPL with the commands you listed.
- Treat "ensign ..." / "ensign, ..." as NPC chat (not just echo).
- Load Ensign persona, style, and replies from ensign.json if present.
- No third-party imports. Safe defaults if JSON is missing.
- Defensive dispatch: unknown or unimplemented features don’t crash.

Python 3.9+.
"""

from __future__ import annotations

import json
import math
import random
import re
import shlex
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


PROMPT = "FalklandV2> "

HELP_TEXT = """Commands:
  status / nav / grid / setpos / course / speed / stop
  seed <int>, spawn [Label] [Side], schedule <sec> spawn [Label] [Side]
  contacts, clrcontacts, setside <id> <Side>, setthreat <id> <0-10>, priority
  char             Show current character traits
  char reload      Reload ensign.json
  ensign <text>    Ask the Ensign
  Any other text   -> forwarded to Ensign
  hud off|<sec>, pause, resume, timescale [x], tick [sec], reset
  exit | quit
"""


# ---------- Data models ----------

SIDES = ("Blue", "Red", "Neutral")


@dataclass
class Character:
    name: str = "Ensign"
    traits: Dict[str, int] = field(default_factory=lambda: {"discipline": 7, "initiative": 6, "clarity": 6})

    def __str__(self) -> str:
        kv = ", ".join(f"{k}={v}" for k, v in self.traits.items())
        return f"{self.name}({kv})"


@dataclass
class Contact:
    id: str
    label: str
    side: str = "Neutral"
    threat: float = 0.0
    priority: float = 0.0


@dataclass
class GameState:
    seed: Optional[int] = None
    character: Optional[Character] = None
    course_deg: float = 0.0
    speed_kts: float = 0.0
    x: float = 0.0
    y: float = 0.0
    contacts: Dict[str, Contact] = field(default_factory=dict)

    def summary(self) -> str:
        pos = f"({self.x:.1f}, {self.y:.1f})"
        cts = f"{len(self.contacts)} contacts"
        char = str(self.character) if self.character else "None"
        return f"COG={self.course_deg:.1f}° SOG={self.speed_kts:.1f} kts POS={pos} | {cts} | Char={char}"

    def set_seed(self, n: int) -> None:
        self.seed = n
        random.seed(n)


# ---------- Ensign brain (JSON-backed) ----------

@dataclass
class EnsignBrain:
    name: str = "Ensign"
    callsign: str = "Ops"
    style: str = "crisp, respectful, Royal Navy 1982"
    max_brief: int = 2
    patterns: Dict[str, List[str]] = field(default_factory=dict)
    smalltalk: List[str] = field(default_factory=list)

def _default_brain() -> EnsignBrain:
    return EnsignBrain(
        patterns={
            "status": [
                "{sir} Status report: COG {cog:.0f}°, SOG {sog:.1f} kts, pos {x:.1f},{y:.1f}. {contacts} contacts on plot."
            ],
            "threat": [
                "{sir} Threat picture: {threat_brief}."
            ],
            "orders": [
                "Aye aye. Executing: {utterance}."
            ],
            "smalltalk": [
                "{sir} {smalltalk}"
            ],
            "unknown": [
                "{sir} Noted. {brief}",
                "{sir} Recommend you specify bearing, range, or desired action."
            ],
        },
        smalltalk=[
            "Standing by.",
            "Aye.",
            "Very good."
        ]
    )

def load_ensign_brain(path: Path) -> EnsignBrain:
    if not path.exists():
        return _default_brain()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _default_brain()
    return EnsignBrain(
        name=data.get("name", "Ensign"),
        callsign=data.get("callsign", "Ops"),
        style=data.get("style", "crisp, respectful, Royal Navy 1982"),
        max_brief=int(data.get("max_brief", 2)),
        patterns=data.get("patterns", {}) or _default_brain().patterns,
        smalltalk=data.get("smalltalk", []) or _default_brain().smalltalk,
    )

def load_character(brain: EnsignBrain) -> Character:
    return Character(name=brain.name)


# ---------- Scheduler ----------

class Scheduler:
    def __init__(self) -> None:
        self._timers: List[threading.Timer] = []
        self._lock = threading.Lock()

    def in_seconds(self, sec: float, fn: Callable[[], None]) -> None:
        t = threading.Timer(sec, fn)
        with self._lock:
            self._timers.append(t)
        t.start()

    def cancel_all(self) -> None:
        with self._lock:
            for t in self._timers:
                t.cancel()
            self._timers.clear()


# ---------- Game Loop ----------

class GameLoop:
    def __init__(self, state: GameState, sched: Scheduler, brain_path: Path) -> None:
        self.state = state
        self.sched = sched
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._timescale = 1.0
        self._hud_deadline: float = 0.0
        self._brain_path = brain_path
        self._brain = load_ensign_brain(self._brain_path)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FalklandLoop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.sched.cancel_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def set_timescale(self, x: float) -> None:
        self._timescale = max(0.0, x)

    def tick(self, sec: float) -> None:
        self._advance(max(0.0, sec))

    def reset(self) -> None:
        self.state.course_deg = 0.0
        self.state.speed_kts = 0.0
        self.state.x = 0.0
        self.state.y = 0.0

    def hud(self, dur: float) -> None:
        self._hud_deadline = 0.0 if dur <= 0 else time.time() + dur
        print(f"[HUD] {'hidden' if self._hud_deadline == 0 else f'visible for {dur:.1f}s'}")

    def set_position(self, x: float, y: float) -> None:
        self.state.x, self.state.y = x, y

    def set_course(self, deg: float) -> None:
        self.state.course_deg = deg % 360.0

    def set_speed(self, kts: float) -> None:
        self.state.speed_kts = max(0.0, kts)

    def stop_ship(self) -> None:
        self.state.speed_kts = 0.0

    def show_nav(self) -> None:
        print(f"NAV {self.state.summary()}")

    def show_grid(self) -> None:
        xi, yi = int(round(self.state.x)), int(round(self.state.y))
        print(f"GRID: cell≈({xi},{yi}) coarse map")

    def show_priority(self) -> None:
        if not self.state.contacts:
            print("No contacts.")
            return
        ranked = sorted(self.state.contacts.values(), key=lambda c: c.priority, reverse=True)
        for c in ranked[:10]:
            print(f"{c.id:>6} {c.side:<7} THR={c.threat:.1f} PRI={c.priority:.2f}  {c.label}")

    def list_contacts(self) -> None:
        if not self.state.contacts:
            print("No contacts.")
            return
        for c in self.state.contacts.values():
            print(f"{c.id:>6} {c.side:<7} THR={c.threat:.1f} PRI={c.priority:.2f}  {c.label}")

    def clear_contacts(self) -> None:
        self.state.contacts.clear()
        print("Contacts cleared.")

    def set_side(self, cid: str, side: str) -> None:
        if cid not in self.state.contacts:
            print(f"{cid}: not found")
            return
        if side not in SIDES:
            print(f"Side must be one of {SIDES}")
            return
        self.state.contacts[cid].side = side

    def set_threat(self, cid: str, level: float) -> None:
        if cid not in self.state.contacts:
            print(f"{cid}: not found")
            return
        level = max(0.0, min(10.0, level))
        self.state.contacts[cid].threat = level
        dx = self.state.x - random.random() * 5
        dy = self.state.y - random.random() * 5
        dist = math.hypot(dx, dy) + 1e-6
        self.state.contacts[cid].priority = level + 5.0 / dist

    def spawn(self, label: Optional[str] = None, side: Optional[str] = None) -> None:
        idx = len(self.state.contacts) + 1
        cid = f"C{idx:03d}"
        lbl = label or f"Unknown-{idx}"
        s = side if side in SIDES else "Neutral"
        c = Contact(id=cid, label=lbl, side=s, threat=0.0, priority=0.0)
        self.state.contacts[cid] = c
        print(f"Spawned {cid} {s} {lbl}")

    # ---------- Ensign dialogue ----------

    def reload_brain(self) -> None:
        self._brain = load_ensign_brain(self._brain_path)

    def _threat_brief(self) -> str:
        if not self.state.contacts:
            return "no declared threats"
        worst = sorted(self.state.contacts.values(), key=lambda c: (c.threat, c.priority), reverse=True)[:3]
        return ", ".join(f"{c.id} {c.side} THR {c.threat:.0f}" for c in worst)

    def _brief(self) -> str:
        options = ["Standing by.", "Awaiting your orders.", "Recommend bearings and intentions, sir."]
        return random.choice(options)

    def ask_ensign(self, text: str) -> None:
        u = (text or "").strip()
        brain = self._brain

        if not u:
            msg = random.choice(brain.smalltalk) if brain.smalltalk else "Standing by."
            print(f"[{brain.name}] {msg}")
            return

        intent = "unknown"
        if re.search(r"\b(status|report|sitrep)\b", u, re.I):
            intent = "status"
        elif re.search(r"\b(threat|air picture|surface picture|contacts?)\b", u, re.I):
            intent = "threat"
        elif re.search(r"\b(set|change|engage|come to|increase|decrease|turn|speed|course)\b", u, re.I):
            intent = "orders"
        elif re.search(r"\b(hello|hi|aye|ready|standing by)\b", u, re.I):
            intent = "smalltalk"

        ctx = {
            "sir": "Sir," if "sir" not in u.lower() else "",
            "cog": self.state.course_deg,
            "sog": self.state.speed_kts,
            "x": self.state.x,
            "y": self.state.y,
            "contacts": len(self.state.contacts),
            "threat_brief": self._threat_brief(),
            "utterance": u,
            "smalltalk": self._brief(),
            "brief": self._brief(),
        }

        # pick a line for intent, or fall back to unknown
        lines = brain.patterns.get(intent) or brain.patterns.get("unknown") or _default_brain().patterns["unknown"]
        line = random.choice(lines)
        print(f"[{brain.name}] {line.format(**ctx)}")


# ---------- Command dispatcher ----------

def handle_command(line: str, state: GameState, sched: Scheduler, loop: GameLoop) -> None:
    # Treat "ensign ..." and "ensign, ..." as chat to Ensign automatically
    m = re.match(r"^\s*ensign\b[\s,:-]*(.*)$", line, re.IGNORECASE)
    if m:
        loop.ask_ensign(m.group(1))
        return

    parts = shlex.split(line)
    if not parts:
        return
    cmd, *args = parts
    cmd = cmd.lower()

    if cmd in {"help", "?"}:
        print(HELP_TEXT); return
    if cmd in {"exit", "quit"}:
        raise SystemExit(0)
    if cmd == "status":
        print(state.summary()); return

    if cmd == "nav":
        loop.show_nav(); return
    if cmd == "grid":
        loop.show_grid(); return
    if cmd == "setpos":
        if len(args) != 2:
            print("usage: setpos <x> <y>"); return
        try:
            x, y = float(args[0]), float(args[1])
        except ValueError:
            print("setpos: x and y must be numbers"); return
        loop.set_position(x, y); return
    if cmd == "course":
        if not args:
            print("usage: course <degrees>"); return
        try:
            hdg = float(args[0])
        except ValueError:
            print("course: degrees must be a number"); return
        loop.set_course(hdg); return
    if cmd == "speed":
        if not args:
            print("usage: speed <knots>"); return
        try:
            kts = float(args[0])
        except ValueError:
            print("speed: knots must be a number"); return
        loop.set_speed(kts); return
    if cmd == "stop":
        loop.stop_ship(); return

    if cmd == "pause":
        loop.pause(); return
    if cmd == "resume":
        loop.resume(); return
    if cmd == "timescale":
        if not args:
            print("usage: timescale <x>"); return
        try:
            x = float(args[0])
        except ValueError:
            print("timescale: x must be a number"); return
        loop.set_timescale(x); return
    if cmd == "tick":
        sec = float(args[0]) if args else 1.0
        loop.tick(sec); return
    if cmd == "reset":
        loop.reset(); return
    if cmd == "hud":
        if not args:
            print("usage: hud off|<seconds>"); return
        if args[0] == "off":
            loop.hud(0.0)
        else:
            try:
                dur = float(args[0])
            except ValueError:
                print("hud: expected 'off' or <seconds>"); return
            loop.hud(dur)
        return

    if cmd == "seed":
        if not args:
            print("usage: seed <int>"); return
        try:
            n = int(args[0])
        except ValueError:
            print("seed: expected integer"); return
        state.set_seed(n); print(f"Seed set to {n}"); return
    if cmd == "spawn":
        label = args[0] if args else None
        side = args[1] if len(args) > 1 else None
        loop.spawn(label=label, side=side); return
    if cmd == "schedule":
        if len(args) >= 2 and args[1] == "spawn":
            try:
                sec = float(args[0])
            except ValueError:
                print("schedule: first arg must be seconds"); return
            label = args[2] if len(args) > 2 else None
            side = args[3] if len(args) > 3 else None
            def _task() -> None:
                loop.spawn(label=label, side=side)
            sched.in_seconds(sec, _task)
            print(f"Scheduled spawn in {sec:.1f}s")
            return
        print("usage: schedule <sec> spawn [Label] [Side]"); return
    if cmd == "contacts":
        loop.list_contacts(); return
    if cmd == "clrcontacts":
        loop.clear_contacts(); return
    if cmd == "setside":
        if len(args) != 2:
            print("usage: setside <id> <Side>"); return
        cid, side = args
        loop.set_side(cid, side); return
    if cmd == "setthreat":
        if len(args) != 2:
            print("usage: setthreat <id> <0-10>"); return
        cid, level = args
        try:
            lvl = float(level)
        except ValueError:
            print("setthreat: level must be a number"); return
        loop.set_threat(cid, lvl); return
    if cmd == "priority":
        loop.show_priority(); return

    if cmd == "char":
        if args == ["reload"]:
            loop.reload_brain()
            state.character = load_character(loop._brain)
            print("Character reloaded from ensign.json." if (Path(__file__).parent / "ensign.json").exists() else "Character reset to default.")
            return
        print(state.character if state.character else "No character loaded")
        return

    if cmd == "ensign":
        text = " ".join(args) if args else ""
        loop.ask_ensign(text)
        return

    # Fallback: free text to Ensign
    loop.ask_ensign(line)


# ---------- Main ----------

def main(argv: List[str]) -> int:
    here = Path(__file__).parent
    brain_path = here / "ensign.json"

    state = GameState()
    sched = Scheduler()
    loop = GameLoop(state, sched, brain_path)
    state.character = load_character(loop._brain)

    loop.start()
    print("FalklandV2 ready. Type 'help' for commands.")

    try:
        while True:
            try:
                line = input(PROMPT)
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line.strip():
                continue
            if line.lower() in ("exit", "quit"):
                break

            handle_command(line, state, sched, loop)
    finally:
        loop.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))