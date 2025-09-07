#!/usr/bin/env python3
"""
Contacts subsystem (Step 5) — spawn, move, cull.

What this file does by itself:
- Loads grid/game config (data/game.json) and spawn rules (data/spawn_rules.json).
- Loads contact templates (data/contacts.json).
- Provides pure functions to:
    * pick weighted templates
    * spawn a contact outside a safety ring around Glamorgan (enemy: ≥10 nm; other: ≥5 nm)
    * compute headings (enemy points toward the ship on spawn)
    * step contacts each tick at 0.75× real speed (spec)
    * cull contacts that leave the map

It does NOT touch engine yet. Engine will call:
    - load_catalog()
    - spawn_random_contact()
    - step_all()
    - cull_offmap()

Run this file directly to see a quick demo (spawns a few contacts and moves them).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json, math, random, time

ROOT = Path(__file__).resolve().parents[1]  # projects/FalklandV2
DATA = ROOT / "data"
STATE = ROOT / "state"

# ---------- Shared grid model ----------

@dataclass(frozen=True)
class GridCfg:
    cols: int = 26
    rows: int = 26
    cell_nm: float = 1.0

@dataclass
class Contact:
    id: int
    name: str
    type: str
    allegiance: str
    speed_kts_real: float
    speed_kts_game: float
    x: float
    y: float
    course_deg: float
    last_course_update_s: float = 0.0
    course_update_period_s: float = 300.0  # 5 minutes per spec
    # book-keeping
    spawned_at_s: float = 0.0

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_game_and_rules() -> Tuple[Dict[str,Any], Dict[str,Any]]:
    game = _load_json(DATA / "game.json")
    # spawn rules may not exist in early steps; fall back to defaults
    try:
        rules = _load_json(DATA / "spawn_rules.json")
    except FileNotFoundError:
        rules = {
            "max_active_contacts": 10,
            "enemy_spawn_min_nm": 10,
            "other_spawn_min_nm": 5,
            "contact_speed_scalar": 0.75,
            "course_change_minutes": 5
        }
    return game, rules

def load_catalog() -> List[Dict[str,Any]]:
    """Reads data/contacts.json."""
    return _load_json(DATA / "contacts.json")

# ---------- Grid helpers (keep in sync with nav.py conventions) ----------

def parse_cell(cell: str) -> Tuple[int,int]:
    col = cell[0].upper()
    row = int(cell[1:])
    x = ord(col) - ord('A')
    y = row - 1
    if not (0 <= x < 26 and 0 <= y < 26):
        raise ValueError(f"Bad cell: {cell}")
    return x, y

def format_cell(x: int, y: int) -> str:
    return f"{chr(ord('A') + x)}{y+1}"

def xy_to_nm(dx_cells: float, dy_cells: float, grid: GridCfg) -> float:
    return math.hypot(dx_cells, dy_cells) * grid.cell_nm

def dist_nm_xy(ax: float, ay: float, bx: float, by: float, grid: GridCfg) -> float:
    return xy_to_nm(bx-ax, by-ay, grid)

def heading_deg(ax: float, ay: float, bx: float, by: float) -> float:
    """Return course (0=N, 90=E) from A to B using nav convention."""
    dx = bx - ax
    dy = by - ay
    # course = arctan2(East, North)
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0

def step_xy(x: float, y: float, course_deg: float, speed_kts: float, dt_s: float, grid: GridCfg) -> Tuple[float,float]:
    # distance in cells for dt
    d_nm = speed_kts * (dt_s / 3600.0)
    d_cells = d_nm / max(1e-9, grid.cell_nm)
    rad = math.radians(course_deg % 360.0)
    dx = d_cells * math.sin(rad)
    dy = d_cells * math.cos(rad)
    nx = max(0.0, min(grid.cols - 1e-6, x + dx))
    ny = max(0.0, min(grid.rows - 1e-6, y + dy))
    return nx, ny

# ---------- Spawning ----------

def _weighted_choice(items: List[Dict[str,Any]], key: str) -> Dict[str,Any]:
    ws = [max(0.0, float(it.get(key, 1))) for it in items]
    total = sum(ws)
    if total <= 0.0:
        # fallback uniform
        return random.choice(items)
    r = random.random() * total
    acc = 0.0
    for it, w in zip(items, ws):
        acc += w
        if r <= acc:
            return it
    return items[-1]

def _pick_spawn_cell_away_from(
    ship_x: float, ship_y: float, min_nm: float, grid: GridCfg, max_tries: int = 200
) -> Tuple[float,float]:
    """Pick a random cell center at least min_nm from ship, inside the grid."""
    for _ in range(max_tries):
        x = random.uniform(0.0, grid.cols - 1e-6)
        y = random.uniform(0.0, grid.rows - 1e-6)
        if dist_nm_xy(ship_x, ship_y, x, y, grid) >= min_nm:
            return x, y
    # Fallback: push to nearest boundary beyond min_nm along random heading
    ang = random.random() * 360.0
    dx = math.sin(math.radians(ang)) * (min_nm / grid.cell_nm)
    dy = math.cos(math.radians(ang)) * (min_nm / grid.cell_nm)
    x = max(0.0, min(grid.cols - 1e-6, ship_x + dx))
    y = max(0.0, min(grid.rows - 1e-6, ship_y + dy))
    return x, y

class ContactPool:
    """Holds active contacts and provides spawn/move/cull operations."""
    def __init__(self, grid: GridCfg, speed_scalar: float = 0.75, course_change_minutes: float = 5.0):
        self.grid = grid
        self.speed_scalar = speed_scalar
        self.course_update_period_s = max(1.0, course_change_minutes * 60.0)
        self.contacts: List[Contact] = []
        self._next_id = 1

    def spawn_random_contact(
        self,
        catalog: List[Dict[str,Any]],
        ship_x: float,
        ship_y: float,
        enemy_min_nm: float,
        other_min_nm: float,
        now_s: float = 0.0
    ) -> Contact:
        """Pick a weighted template and instantiate a Contact."""
        template = _weighted_choice(catalog, "weight")
        allegiance = str(template["allegiance"])
        min_nm = enemy_min_nm if allegiance.lower() == "hostile" else other_min_nm

        x, y = _pick_spawn_cell_away_from(ship_x, ship_y, min_nm, self.grid)

        # Initial course: enemies head toward ship; others pick a random heading
        if allegiance.lower() == "hostile":
            crs = heading_deg(x, y, ship_x, ship_y)
        else:
            crs = random.random() * 360.0

        real = float(template.get("speed_kts", template.get("Speed (kts)", 0.0)))
        game = real * self.speed_scalar

        c = Contact(
            id=self._next_id, name=template["name"], type=template["type"],
            allegiance=allegiance, speed_kts_real=real, speed_kts_game=game,
            x=float(x), y=float(y), course_deg=float(crs),
            last_course_update_s=now_s, course_update_period_s=self.course_update_period_s,
            spawned_at_s=now_s
        )
        self.contacts.append(c)
        self._next_id += 1
        return c

    def step_all(self, dt_s: float, ship_x: float, ship_y: float, now_s: float) -> None:
        """Move all contacts; hostile retargets toward ship each course_update_period."""
        for c in self.contacts:
            # Retarget hostile courses periodically toward ship
            if c.allegiance.lower() == "hostile" and (now_s - c.last_course_update_s) >= c.course_update_period_s:
                c.course_deg = heading_deg(c.x, c.y, ship_x, ship_y)
                c.last_course_update_s = now_s
            c.x, c.y = step_xy(c.x, c.y, c.course_deg, c.speed_kts_game, dt_s, self.grid)

    def cull_offmap(self) -> int:
        """Remove contacts that hit the clamps (edge). Return how many removed."""
        before = len(self.contacts)
        # Because movement clamps to inside a tiny epsilon, treat "scraping" edges as exited if they keep clamping.
        kept: List[Contact] = []
        eps = 1e-5
        for c in self.contacts:
            if (eps <= c.x <= self.grid.cols-1-eps) and (eps <= c.y <= self.grid.rows-1-eps):
                kept.append(c)
        self.contacts = kept
        return before - len(self.contacts)

# ---------- Demo runner (standalone smoke test) ----------

def _demo():
    print("CONTACTS DEMO — spawn & move")
    game, spawn_rules = load_game_and_rules()
    grid = GridCfg(
        cols=game.get("grid",{}).get("cols", 26),
        rows=game.get("grid",{}).get("rows", 26),
        cell_nm=game.get("grid",{}).get("cell_nm", 1.0),
    )

    # Ship position: from state if available, else game.start
    ship_state = {}
    try:
        ship_state = _load_json(STATE / "runtime.json").get("ship", {})
    except FileNotFoundError:
        pass
    cell = ship_state.get("cell", game.get("start", {}).get("ship_cell", "K13"))
    ship_x, ship_y = parse_cell(cell)

    catalog = load_catalog()
    pool = ContactPool(
        grid=grid,
        speed_scalar=float(spawn_rules.get("contact_speed_scalar", 0.75)),
        course_change_minutes=float(spawn_rules.get("course_change_minutes", 5.0)),
    )

    enemy_min = float(spawn_rules.get("enemy_spawn_min_nm", 10.0))
    other_min = float(spawn_rules.get("other_spawn_min_nm", 5.0))

    # Spawn up to 5 contacts (respecting max_active_contacts later when engine calls us)
    now_s = 0.0
    for _ in range(5):
        c = pool.spawn_random_contact(catalog, ship_x, ship_y, enemy_min, other_min, now_s=now_s)
        dist = dist_nm_xy(c.x, c.y, ship_x, ship_y, grid)
        print(f"  SPAWN #{c.id:02d}: {c.name:22s} {c.allegiance:8s} at {format_cell(int(round(c.x)), int(round(c.y)))} "
              f"| {dist:.1f} nm | crs {c.course_deg:.0f}° | {c.speed_kts_game:.0f} kts (0.75×)")

    # Move for 60 seconds in 5-second ticks
    for step in range(12):
        dt = 5.0
        now_s += dt
        pool.step_all(dt_s=dt, ship_x=ship_x, ship_y=ship_y, now_s=now_s)
        removed = pool.cull_offmap()
        # Print brief status
        print(f"[t+{int(now_s):>3d}s] {len(pool.contacts)} contacts | removed {removed}")
        # Show top 3 nearest contacts to ship
        nearest = sorted(pool.contacts, key=lambda c: dist_nm_xy(c.x, c.y, ship_x, ship_y, grid))[:3]
        for c in nearest:
            d = dist_nm_xy(c.x, c.y, ship_x, ship_y, grid)
            print(f"    #{c.id:02d} {c.name:22s} {c.allegiance:8s} d={d:4.1f}nm crs={c.course_deg:3.0f}° at {format_cell(int(round(c.x)), int(round(c.y)))}")
        time.sleep(0.01)  # tiny pause for readability

if __name__ == "__main__":
    _demo()