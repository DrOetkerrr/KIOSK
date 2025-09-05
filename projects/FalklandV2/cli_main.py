#!/usr/bin/env python3
"""
FalklandV2 — Ensign API + realtime sim + contacts + tolerant CLI

Highlights
- Realtime clock (monotonic), pause/resume, timescale, manual tick.
- Quiet HUD by default; 'hud <sec>' to enable heartbeat.
- Grid nav (A–Z x 1–26, 2.0 NM per cell). Dead reckoning via course/speed.
- Contacts (max 15) with side/threat; deterministic spawns via 'seed'.
- Priority target: highest threat, tie-break by nearest.
- Ensign command streams OpenAI reply with current world state.
- Tolerant parsing: 'ensign, …' or 'ensign: …' work.
- Fallback: any unrecognized input is sent to Ensign.

Try:
  status
  seed 42
  setpos K12
  spawn Bogey ENEMY
  contacts
  priority
  ensign Launch report
  what's our nearest threat?
  hud 2
  hud off
  exit
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, List
import math, sys, shlex, threading, time, heapq, re, random, os, json
import requests
from dotenv import load_dotenv
from pathlib import Path

# ---------- Env & constants ----------
# Load .env from repo root even if running from elsewhere:
ROOT = Path(__file__).resolve().parents[2]  # projects/FalklandV2 -> projects -> repo root
load_dotenv(ROOT / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
OPENAI_BASE    = os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")

GRID_COLS = 26
GRID_ROWS = 26
CELL_NM   = 2.0
SPAWN_RING_NM = 10.0

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

def nm_from_cells(dx_cells: float, dy_cells: float) -> float:
    return math.hypot(dx_cells, dy_cells) * CELL_NM

def bearing_T_deg(dx_cells: float, dy_cells: float) -> float:
    dx_nm = dx_cells * CELL_NM
    dy_nm = -dy_cells * CELL_NM  # north positive
    ang = math.degrees(math.atan2(dx_nm, dy_nm))  # atan2(East, North)
    if ang < 0:
        ang += 360.0
    return ang

# ---------- Contacts ----------
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
    return s if s in ("NEUTRAL","FRIENDLY","ENEMY") else "ENEMY"

def default_threat_for_side(side: str) -> int:
    s = normalize_side(side)
    return 5 if s == "ENEMY" else (0 if s == "FRIENDLY" else 1)

# ---------- Core state ----------
@dataclass
class GameState:
    # Clock
    sim_time_s: float = 0.0
    timescale: float = 1.0
    running: bool = True
    # Nav
    x_cells: float = float(col_to_idx("K"))
    y_cells: float = 11.0
    speed_kn: float = 0.0
    course_deg: float = 0.0
    # Contacts
    next_contact_id: int = 1
    contacts: List[Contact] = field(default_factory=list)
    contacts_cap: int = 15
    # Misc
    mode: str = "SIM"
    rng: random.Random = field(default_factory=random.Random)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # HUD/time
    def grid_pos(self) -> Tuple[str,int]:
        xi = int(clamp(math.floor(self.x_cells + 1e-9), 0, GRID_COLS - 1))
        yi = int(clamp(math.floor(self.y_cells + 1e-9), 0, GRID_ROWS - 1))
        return (idx_to_col(xi), yi + 1)
    def timecode(self) -> str:
        return f"H+{int(self.sim_time_s // 3600)}"
    def hud_line(self) -> str:
        col, row = self.grid_pos()
        return f"{self.timecode()} | Pos {col}{row} | Spd {self.speed_kn:.1f} kn | Crs {self.course_deg:.0f}° | Contacts {len(self.contacts)} | Mode {self.mode} | x{self.timescale:.2f}"
    def reset_world(self) -> None:
        self.sim_time_s = 0.0; self.timescale = 1.0; self.running = True
        self.x_cells = float(col_to_idx("K")); self.y_cells = 11.0
        self.speed_kn = 0.0; self.course_deg = 0.0
        self.next_contact_id = 1; self.contacts.clear()
        self.mode = "SIM"

    # Contact helpers
    def spawn_contact(self, label: str = "Contact", side: str = "ENEMY") -> Contact:
        if len(self.contacts) >= self.contacts_cap:
            raise RuntimeError(f"Contact cap reached ({self.contacts_cap}).")
        brg = self.rng.uniform(0.0, 360.0)
        dy_nm = math.cos(math.radians(brg)) * SPAWN_RING_NM
        dx_nm = math.sin(math.radians(brg)) * SPAWN_RING_NM
        dx_cells = dx_nm / CELL_NM
        dy_cells = dy_nm / CELL_NM
        cx = clamp(self.x_cells + dx_cells, 0.0, GRID_COLS - 1e-6)
        cy = clamp(self.y_cells - dy_cells, 0.0, GRID_ROWS - 1e-6)  # north reduces y
        c = Contact(
            cid=self.next_contact_id, label=label,
            side=normalize_side(side), threat=default_threat_for_side(side),
            x_cells=cx, y_cells=cy
        )
        self.next_contact_id += 1
        self.contacts.append(c)
        return c

    def list_contacts(self) -> List[Contact]:
        return list(self.contacts)
    def clear_contacts(self) -> None:
        self.contacts.clear()
    def find_contact(self, cid: int) -> Optional[Contact]:
        for c in self.contacts:
            if c.cid == cid: return c
        return None
    def contact_range_bearing(self, c: Contact) -> Tuple[float, float]:
        dx = c.x_cells - self.x_cells
        dy = c.y_cells - self.y_cells
        return nm_from_cells(dx, dy), bearing_T_deg(dx, dy)
    def priority_contact(self) -> Optional[Contact]:
        if not self.contacts: return None
        max_thr = max(c.threat for c in self.contacts)
        cands = [c for c in self.contacts if c.threat == max_thr]
        return min(cands, key=lambda c: self.contact_range_bearing(c)[0])

# ---------- Scheduler ----------
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
        with self._lock: heapq.heappush(self._pq, ev)
    def pop_due(self, now_s: float) -> List[ScheduledEvent]:
        out: List[ScheduledEvent] = []
        with self._lock:
            while self._pq and self._pq[0].due_time_s <= now_s:
                out.append(heapq.heappop(self._pq))
        return out
    def clear(self) -> None:
        with self._lock: self._pq.clear()

# ---------- Loop ----------
class GameLoop:
    def __init__(self, state: GameState, scheduler: Scheduler, print_fn=print):
        self.state = state; self.scheduler = scheduler; self.print = print_fn
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="GameLoop", daemon=True)
        self._last_wall = time.monotonic(); self._last_sim_s = self.state.sim_time_s
        self._hud_interval_s = 0.0; self._hud_next_s = 0.0
        self._cadence_s = 0.1
    def start(self): self._thread.start()
    def stop(self): self._stop_evt.set(); self._thread.join(timeout=2.0)
    def pause(self): self.state.running = False
    def resume(self):
        self._last_wall = time.monotonic(); self._last_sim_s = self.state.sim_time_s; self.state.running = True
    def set_timescale(self, s: float): self.state.timescale = max(0.0, s)
    def set_hud_interval(self, sec: float):
        self._hud_interval_s = max(0.0, sec)
        if self._hud_interval_s > 0.0:
            self._hud_next_s = self.state.sim_time_s + self._hud_interval_s
    def jump(self, d: float):
        ds = max(0.0, d); self.state.sim_time_s += ds
        self._integrate_nav(ds); self._trigger_due_events(); self._maybe_heartbeat()
    def reset(self):
        self.state.reset_world(); self.scheduler.clear(); self._hud_next_s = 0.0
        self._last_wall = time.monotonic(); self._last_sim_s = self.state.sim_time_s
    def _run(self):
        while not self._stop_evt.is_set():
            now_wall = time.monotonic(); dt_wall = now_wall - self._last_wall; self._last_wall = now_wall
            if self.state.running and self.state.timescale > 0.0:
                self.state.sim_time_s += dt_wall * self.state.timescale
                dt_sim = self.state.sim_time_s - self._last_sim_s
                if dt_sim > 0:
                    self._integrate_nav(dt_sim); self._trigger_due_events(); self._maybe_heartbeat()
                    self._last_sim_s = self.state.sim_time_s
            time.sleep(self._cadence_s)
    def _integrate_nav(self, dt: float):
        if self.state.speed_kn <= 0.0 or dt <= 0.0: return
        dist_nm = self.state.speed_kn * (dt / 3600.0)
        ang = math.radians(self.state.course_deg % 360.0)
        dy_nm = math.cos(ang) * dist_nm; dx_nm = math.sin(ang) * dist_nm
        dx_cells = dx_nm / CELL_NM; dy_cells = dy_nm / CELL_NM
        new_x = self.state.x_cells + dx_cells; new_y = self.state.y_cells - dy_cells
        cx = clamp(new_x, 0.0, GRID_COLS - 1e-6); cy = clamp(new_y, 0.0, GRID_ROWS - 1e-6)
        hit_edge = (abs(cx - new_x) > 1e-9) or (abs(cy - new_y) > 1e-9)
        self.state.x_cells = cx; self.state.y_cells = cy
        if hit_edge and self.state.speed_kn > 0.0:
            self.state.speed_kn = 0.0; self.print(f"[{self.state.timecode()}] NAV: Edge reached at {cell_name(int(cx), int(cy))}. Speed set to 0.")
    def _trigger_due_events(self):
        for ev in self.scheduler.pop_due(self.state.sim_time_s):
            parts = (ev.payload or "").split() if ev.payload else []
            side = parts[-1] if parts and parts[-1].upper() in ("NEUTRAL","FRIENDLY","ENEMY") else "ENEMY"
            label = " ".join(parts[:-1]) if parts and parts[-1].upper() in ("NEUTRAL","FRIENDLY","ENEMY") else (ev.payload or ev.label)
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
            self.print(self.state.hud_line()); self._hud_next_s = self.state.sim_time_s + self._hud_interval_s

# ---------- OpenAI chat (streaming) ----------
def build_system_prompt(state: GameState) -> str:
    col,row = state.grid_pos()
    pri = state.priority_contact()
    contacts_block = []
    for c in state.contacts:
        rng, brg = state.contact_range_bearing(c)
        contacts_block.append(f"#{c.cid} {c.label} {c.side} {c.cell_name()} rng {rng:.1f}NM brg {brg:.0f}°T thr {c.threat}")
    contacts_txt = "(none)" if not contacts_block else "\n".join(contacts_block)
    pri_txt = "(none)" if not pri else f"#{pri.cid} {pri.label} {pri.side} at {pri.cell_name()} rng≈{state.contact_range_bearing(pri)[0]:.1f}NM thr={pri.threat}"
    guidance = (
        "You are the ship's Ensign. Reply concisely like a capable watch officer.\n"
        "- Use short, clear sentences. Avoid roleplay flourishes unless asked.\n"
        "- If asked for advice or status, reference timecode and priority target.\n"
        "- Don't invent facts; use only what's provided below.\n"
    )
    world = (
        f"Time: {state.timecode()} | Pos {col}{row} | Spd {state.speed_kn:.1f} kn | Crs {state.course_deg:.0f}°\n"
        f"Contacts ({len(state.contacts)}/{state.contacts_cap}):\n{contacts_txt}\n"
        f"Priority: {pri_txt}\n"
    )
    return guidance + "\n" + world

def stream_chat(system_prompt: str, user_text: str):
    if not OPENAI_API_KEY:
        print("! OPENAI_API_KEY missing. Create a .env with OPENAI_API_KEY=... and restart.")
        return
    url = f"{OPENAI_BASE}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.5,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    }
    with requests.post(url, headers=headers, json=payload, stream=True, timeout=60) as r:
        r.raise_for_status()
        print("Ensign:", end=" ", flush=True)
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"].get("content")
                    if delta:
                        print(delta, end="", flush=True)
                except Exception:
                    continue
        print("")  # newline

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
  spawn [Label] [Side]         Spawn at 10 NM ring (Side: ENEMY|NEUTRAL|FRIENDLY)
  schedule <sec> spawn [Label] [Side]
  contacts                     List contacts
  clrcontacts                  Remove all contacts
  setside <id> <Side>          Change side for a contact
  setthreat <id> <0-10>        Set threat level (0..10)
  priority                     Show priority target

  ensign <text>                Ask the Ensign (streams reply).
  Any other text is forwarded to the Ensign automatically.

  pause | resume               Control realtime clock
  timescale [factor]           Show/set simulation speed (1 = realtime)
  tick [sec]                   Manually advance sim time by N seconds
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

    print("FalklandV2 realtime + nav + contacts + Ensign (quiet HUD). Type 'help' for commands.")
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

            raw_line = line.strip()
            raw_cmd = parts[0].lower()
            cmd_norm = re.sub(r'[^a-z]', '', raw_cmd)  # 'ensign,' -> 'ensign'
            args = parts[1:]

            try:
                if cmd_norm in ("exit","quit"):
                    break

                elif cmd_norm == "help":
                    print(HELP_TEXT, end="")

                elif cmd_norm == "status":
                    print(state.hud_line())

                elif cmd_norm == "hud":
                    if not args:
                        print("Usage: hud off | <seconds>")
                    elif args[0].lower() == "off":
                        loop.set_hud_interval(0.0); print("HUD heartbeat off.")
                    else:
                        sec = max(0.1, parse_float(args[0], 1.0))
                        loop.set_hud_interval(sec); print(f"HUD heartbeat every {sec:.1f}s.")

                elif cmd_norm == "nav":
                    col,row = state.grid_pos()
                    print(f"Cell {col}{row} | x={state.x_cells:.3f} y={state.y_cells:.3f} | Spd {state.speed_kn:.2f} kn | Crs {state.course_deg:.1f}°")

                elif cmd_norm == "grid":
                    print(f"Grid A–Z x 1–26, cell size {CELL_NM} NM")

                elif cmd_norm == "setpos":
                    if not args: print("Usage: setpos <Cell>"); continue
                    x_idx, y_idx = parse_cell(args[0])
                    state.x_cells = float(x_idx); state.y_cells = float(y_idx)
                    print(f"Position set to {cell_name(x_idx,y_idx)}")

                elif cmd_norm == "course":
                    if not args: print(f"Course {state.course_deg:.1f}°")
                    else:
                        deg = parse_float(args[0], state.course_deg)
                        state.course_deg = deg % 360.0; print(f"Course set to {state.course_deg:.1f}°")

                elif cmd_norm == "speed":
                    if not args: print(f"Speed {state.speed_kn:.2f} kn")
                    else:
                        kn = max(0.0, parse_float(args[0], state.speed_kn))
                        state.speed_kn = kn; print(f"Speed set to {state.speed_kn:.2f} kn")

                elif cmd_norm == "stop":
                    state.speed_kn = 0.0; print("Speed set to 0 kn")

                elif cmd_norm == "seed":
                    if not args: print("Usage: seed <int>")
                    else: state.rng.seed(parse_int(args[0],0)); print("Seed set.")

                elif cmd_norm == "spawn":
                    label = args[0] if args else "Contact"
                    side = args[1] if len(args) > 1 else "ENEMY"
                    try:
                        c = state.spawn_contact(label=label, side=side)
                        print(f"Spawned #{c.cid} {c.label} {c.side} at {c.cell_name()} (thr {c.threat})")
                    except Exception as e:
                        print(f"! spawn failed: {e}")

                elif cmd_norm == "contacts":
                    if not state.contacts: print("(no contacts)")
                    else:
                        for c in state.contacts:
                            rng_nm, brg = state.contact_range_bearing(c)
                            print(f"#{c.cid:02d} {c.label:12s} {c.side:9s} {c.cell_name():>3s}  rng {rng_nm:5.1f} NM  brg {brg:6.1f}°T  thr {c.threat}")

                elif cmd_norm == "clrcontacts":
                    state.clear_contacts(); print("Contacts cleared.")

                elif cmd_norm == "setside":
                    if len(args) < 2: print("Usage: setside <id> <NEUTRAL|FRIENDLY|ENEMY>")
                    else:
                        cid = parse_int(args[0], 0); side = normalize_side(args[1])
                        c = state.find_contact(cid)
                        if not c: print(f"! no contact with id {cid}")
                        else: c.side = side; print(f"#{c.cid} side set to {c.side}")

                elif cmd_norm == "setthreat":
                    if len(args) < 2: print("Usage: setthreat <id> <0-10>")
                    else:
                        cid = parse_int(args[0], 0); thr = int(clamp(parse_int(args[1],0),0,10))
                        c = state.find_contact(cid)
                        if not c: print(f"! no contact with id {cid}")
                        else: c.threat = thr; print(f"#{c.cid} threat set to {c.threat}")

                elif cmd_norm == "priority":
                    pc = state.priority_contact()
                    if not pc: print("(no priority target)")
                    else:
                        rng_nm, brg = state.contact_range_bearing(pc)
                        print(f"PRIORITY -> #{pc.cid} {pc.label} {pc.side} at {pc.cell_name()} | rng {rng_nm:.1f} NM | brg {brg:.1f}°T | thr {pc.threat}")

                elif cmd_norm == "schedule":
                    if len(args) < 2:
                        print("Usage: schedule <sec> <label> [payload]"); continue
                    delay_s = parse_float(args[0], 0.0); label = args[1]
                    payload = " ".join(args[2:]) if len(args) > 2 else None
                    sched.schedule_in(delay_s, label, payload, now_s=state.sim_time_s)
                    due = state.sim_time_s + delay_s
                    print(f"Scheduled '{label}' in {delay_s:.1f}s (t={due:.1f})")

                elif cmd_norm == "pause":
                    loop.pause(); print("Paused.")
                elif cmd_norm == "resume":
                    loop.resume(); print("Resumed.")

                elif cmd_norm == "timescale":
                    if not args: print(f"Timescale x{state.timescale:.2f}")
                    else: loop.set_timescale(parse_float(args[0],1.0)); print(f"Timescale set to x{state.timescale:.2f}")

                elif cmd_norm == "tick":
                    delta = parse_float(args[0] if args else None, 1.0)
                    loop.jump(delta); print(state.hud_line())

                elif cmd_norm == "reset":
                    loop.reset(); print("Reset OK."); print(state.hud_line())

                elif cmd_norm == "ensign":
                    if not args:
                        print("Usage: ensign <message>")
                    else:
                        user_text = " ".join(args)
                        sys_prompt = build_system_prompt(state)
                        try:
                            stream_chat(sys_prompt, user_text)
                        except requests.HTTPError as e:
                            print(f"! API HTTP error: {e}")
                        except requests.RequestException as e:
                            print(f"! Network error: {e}")

                else:
                    # Free-chat fallback: send the whole line to Ensign
                    sys_prompt = build_system_prompt(state)
                    try:
                        stream_chat(sys_prompt, raw_line)
                    except requests.HTTPError as e:
                        print(f"! API HTTP error: {e}")
                    except requests.RequestException as e:
                        print(f"! Network error: {e}")

            except Exception as e:
                print(f"! error: {e}")
    finally:
        loop.stop()
        print("Goodbye.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))