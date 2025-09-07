#!/usr/bin/env python3
"""
Falklands V2 — Engine (Log level support)

New:
- Read log_level from data/game.json: 'silent' | 'quiet' | 'normal' | 'debug'
- All engine prints now go through _log(<channel>, msg)
  channels we use: 'alert', 'radar', 'spawn', 'status', 'autosave'
  Mapping by level:
    silent → (prints nothing)
    quiet  → only 'alert'
    normal → alert, radar, spawn, status, autosave (default)
    debug  → same as normal (reserved for future extra noise)

Everything else (nav, contact spawn/move/cull, radar) unchanged.
"""

from __future__ import annotations
import json, time, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE = ROOT / "state"
RUNTIME = STATE / "runtime.json"
GAMECFG = DATA / "game.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from subsystems import nav
from subsystems import contacts as cons
from subsystems import radar as rdar

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")

@dataclass
class Schedulers:
    next_radar_scan_s: float = 0.0
    next_autosave_s: float = 5.0

class Engine:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or ROOT
        self.data = self.root / "data"
        self.state_dir = self.root / "state"

        self.game_cfg: Dict[str, Any] = _load_json(self.data / "game.json")
        self.state: Dict[str, Any] = _load_json(self.state_dir / "runtime.json")

        # ----- logging level
        self.log_level: str = str(self.game_cfg.get("log_level", "normal")).lower()
        self._log_matrix = {
            "silent": set(),                           # nothing
            "quiet": {"alert"},                        # only critical alerts
            "normal": {"alert","radar","spawn","status","autosave"},
            "debug": {"alert","radar","spawn","status","autosave"},  # future expansion
        }

        # Grid + nav
        grid = self.game_cfg.get("grid", {"cols": 26, "rows": 26, "cell_nm": 1.0})
        self.grid_cfg = nav.GridCfg(cols=grid["cols"], rows=grid["rows"], cell_nm=grid["cell_nm"])

        ship = self.state.setdefault("ship", {})
        if not ship.get("pos"):
            cell = ship.get("cell", self.game_cfg.get("start", {}).get("ship_cell", "K13"))
            pos0 = nav.from_cell_center(cell)
            ship["pos"] = {"x": pos0.x, "y": pos0.y}

        # Rules & catalog
        try:
            self.spawn_rules: Dict[str, Any] = _load_json(self.data / "spawn_rules.json")
        except FileNotFoundError:
            self.spawn_rules = {
                "max_active_contacts": 10,
                "enemy_spawn_min_nm": 10,
                "other_spawn_min_nm": 5,
                "contact_speed_scalar": 0.75,
                "course_change_minutes": 5
            }
        self.catalog: List[Dict[str, Any]] = _load_json(self.data / "contacts.json")

        self.pool = cons.ContactPool(
            grid=cons.GridCfg(self.grid_cfg.cols, self.grid_cfg.rows, self.grid_cfg.cell_nm),
            speed_scalar=float(self.spawn_rules.get("contact_speed_scalar", 0.75)),
            course_change_minutes=float(self.spawn_rules.get("course_change_minutes", 5.0)),
        )

        self.state.setdefault("contacts", [])
        self.state.setdefault("radar", {}).setdefault("locked_contact_id", None)

        self.t: float = 0.0  # engine time (s)

        # Border alert cooldown
        self._border_alert_last_msg: Optional[str] = None
        self._border_alert_next_time: float = 0.0
        self._border_alert_cooldown_s: float = 10.0

        # Schedulers
        scan_min = float(self.game_cfg.get("auto_radar_scan_minutes", 3))
        self.scan_period_s = max(1.0, scan_min * 60.0)
        self.autosave_period_s = 5.0
        self.sched = Schedulers(
            next_radar_scan_s=self.scan_period_s,
            next_autosave_s=self.autosave_period_s
        )

    # ---------- logging helpers

    def _should_log(self, channel: str) -> bool:
        allowed = self._log_matrix.get(self.log_level, self._log_matrix["normal"])
        return channel in allowed

    def _log(self, channel: str, message: str) -> None:
        if self._should_log(channel):
            print(message)

    # ---------- small helpers

    def _ship_xy(self) -> tuple[float, float]:
        p = self.state["ship"]["pos"]
        return float(p["x"]), float(p["y"])

    def _ship_course_speed(self) -> tuple[float, float]:
        s = self.state["ship"]
        return float(s.get("course_deg", 0.0)), float(s.get("speed_kts", 0.0))

    def _contacts_snapshot(self) -> list[dict]:
        sx, sy = self._ship_xy()
        out = []
        for c in self.pool.contacts:
            cell = cons.format_cell(int(round(c.x)), int(round(c.y)))
            rng = cons.dist_nm_xy(c.x, c.y, sx, sy, self.pool.grid)
            out.append({
                "id": c.id, "name": c.name, "type": c.type, "allegiance": c.allegiance,
                "cell": cell, "range_nm": round(rng, 1),
                "course_deg": round(c.course_deg, 0), "speed_kts": round(c.speed_kts_game, 0)
            })
        return out

    # ---------- HUD

    def hud(self) -> str:
        ship = self.state.get("ship", {})
        pos = ship.get("pos", {"x": 0.0, "y": 0.0})
        x_i, y_i = nav.snapped_cell(nav.NavState(pos["x"], pos["y"]))
        cell = nav.format_cell(x_i, y_i)
        course, speed = self._ship_course_speed()
        grid = self.game_cfg.get("grid", {"cols": 26, "rows": 26})
        return (f"GRID {grid.get('cols','?')}x{grid.get('rows','?')} | "
                f"POS={cell} COG={course:.1f}° SOG={speed:.1f} kts | {len(self.pool.contacts)} contacts")

    # ---------- tasks

    def _radar_scan(self) -> None:
        sx, sy = self._ship_xy()
        max_active = int(self.spawn_rules.get("max_active_contacts", 10))
        enemy_min = float(self.spawn_rules.get("enemy_spawn_min_nm", 10.0))
        other_min = float(self.spawn_rules.get("other_spawn_min_nm", 5.0))

        need = max(0, max_active - len(self.pool.contacts))
        if need > 0:
            spawned = []
            for _ in range(need):
                c = self.pool.spawn_random_contact(
                    catalog=self.catalog, ship_x=sx, ship_y=sy,
                    enemy_min_nm=enemy_min, other_min_nm=other_min, now_s=self.t
                )
                rng = cons.dist_nm_xy(c.x, c.y, sx, sy, self.pool.grid)
                cell = cons.format_cell(int(round(c.x)), int(round(c.y)))
                spawned.append((c, rng, cell))
            for c, rng, cell in spawned:
                self._log("spawn",
                          f"[t+{int(self.t)}s] NEW CONTACT: {cell} {c.name} ({c.allegiance}) d={rng:.1f} nm | crs {c.course_deg:.0f}° | {c.speed_kts_game:.0f} kts (id #{c.id:02d})")
        # Snapshot and status line
        self.state["contacts"] = self._contacts_snapshot()
        locked = self._validate_or_clear_lock()
        line = rdar.status_line(self.pool, (sx, sy), locked_id=locked, max_list=3)
        self._log("status", f"[t+{int(self.t)}s] {line}")

    def _autosave(self) -> None:
        ship = self.state.get("ship", {})
        pos = ship.get("pos", {"x": 0.0, "y": 0.0})
        x_i, y_i = nav.snapped_cell(nav.NavState(pos["x"], pos["y"]))
        ship["cell"] = nav.format_cell(x_i, y_i)
        self.state["contacts"] = self._contacts_snapshot()
        _save_json(self.state_dir / "runtime.json", self.state)
        self._log("autosave", f"[t+{int(self.t)}s] State autosaved.")

    def _advance_ship(self, dt: float) -> None:
        ship = self.state.setdefault("ship", {})
        pos = ship.setdefault("pos", {})
        course, speed = self._ship_course_speed()
        pos_state = nav.NavState(float(pos.get("x", 0.0)), float(pos.get("y", 0.0)))
        new_pos = nav.step_position(pos_state, course_deg=course, speed_kts=speed, dt_seconds=dt, grid=self.grid_cfg)
        pos["x"], pos["y"] = new_pos.x, new_pos.y

        alert = nav.border_alert(new_pos, course_deg=course, grid=self.grid_cfg, warn_distance_cells=1.0)
        if alert and ((self.t >= self._border_alert_next_time) or (alert != self._border_alert_last_msg)):
            self._log("alert", f"[t+{int(self.t)}s] ALERT: {alert}")
            self._border_alert_last_msg = alert
            self._border_alert_next_time = self.t + self._border_alert_cooldown_s

    def _advance_contacts(self, dt: float) -> None:
        sx, sy = self._ship_xy()
        before = len(self.pool.contacts)
        self.pool.step_all(dt_s=dt, ship_x=sx, ship_y=sy, now_s=self.t)
        removed = self.pool.cull_offmap()
        after = len(self.pool.contacts)
        if removed:
            self._log("radar", f"[t+{int(self.t)}s] RADAR: {removed} contact(s) left the map ({before}→{after}).")
        self.state["contacts"] = self._contacts_snapshot()
        self._validate_or_clear_lock()

    def _validate_or_clear_lock(self) -> Optional[int]:
        rid = self.state["radar"].get("locked_contact_id")
        if rid is None:
            return None
        if any(c.id == rid for c in self.pool.contacts):
            return rid
        self.state["radar"]["locked_contact_id"] = None
        return None

    # ---------- public

    def tick(self, dt: float) -> None:
        self.t += dt
        self._advance_ship(dt)
        self._advance_contacts(dt)

        if self.t >= self.sched.next_radar_scan_s:
            self._radar_scan()
            self.sched.next_radar_scan_s += self.scan_period_s

        if self.t >= self.sched.next_autosave_s:
            self._autosave()
            self.sched.next_autosave_s += self.autosave_period_s

# ---------- demo runner (unchanged)

def _demo_run(seconds: int = 20) -> None:
    if not GAMECFG.exists() or not RUNTIME.exists():
        print("Config/state missing. Run: python3 projects/FalklandV2/main.py first.")
        return
    eng = Engine()
    print("Engine init OK.")
    print("HUD:", eng.hud())
    tick = float(eng.game_cfg.get("tick_seconds", 1.0))
    loops = int(max(1, seconds) / tick)
    for i in range(loops):
        time.sleep(tick)
        eng.tick(tick)
        print(f"[t+{int((i+1)*tick)}s] {eng.hud()}")

if __name__ == "__main__":
    _demo_run(seconds=20)