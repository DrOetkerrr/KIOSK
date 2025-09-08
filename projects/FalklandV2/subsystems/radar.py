"""
Radar subsystem — fleet presence, spawn rhythm, and reporting (with diagnostics).

- Persistent fleet (outside cap): HMS Hermes + HMS Coventry
  • mirror own ship course/speed
  • remain within ~3 cells
  • if no pool spawn & no candidates: directly fabricate contacts (safe fallback)
- Non-persistent contacts (subject to cap):
  • Friendlies trickle in
  • Hostiles arrive in 3–5 waves with lulls
- manual_scan(...) is report-only (NEVER spawns)
- auto_tick(...) is the ONLY place that spawns and maintains fleet
"""

from __future__ import annotations
import math, random, time
from typing import Dict, List, Optional, Tuple
from types import SimpleNamespace as _NS

from subsystems import contacts

# ------------------------------ Status line ----------------------------------

def status_line(pool, ship_xy: Tuple[float, float], locked_id: Optional[int] = None, max_list: int = 3) -> str:
    sx, sy = ship_xy
    lst = sorted(getattr(pool, "contacts", []), key=lambda c: contacts.dist_nm_xy(c.x, c.y, sx, sy, pool.grid))
    n = len(lst)
    line = f"{n} contact(s)"

    if locked_id:
        tgt = next((c for c in lst if c.id == locked_id), None)
        if tgt:
            rng = round(contacts.dist_nm_xy(tgt.x, tgt.y, sx, sy, pool.grid), 1)
            cell = contacts.format_cell(int(round(tgt.x)), int(round(tgt.y)))
            line += f" | locked: {cell} {tgt.type} {tgt.allegiance} d={rng}nm (#{tgt.id})"

    if lst:
        pieces = []
        for c in lst[:max_list]:
            rng = round(contacts.dist_nm_xy(c.x, c.y, sx, sy, pool.grid), 1)
            cell = contacts.format_cell(int(round(c.x)), int(round(c.y)))
            pieces.append(f"{cell} {c.type} {c.allegiance} d={rng}nm (#{c.id})")
        line += " | " + " | ".join(pieces)

    return "RADAR: " + line

def unlock_contact(state: Dict) -> None:
    state.setdefault("radar", {})["locked_contact_id"] = None

def lock_contact(state: Dict, cid: int) -> None:
    state.setdefault("radar", {})["locked_contact_id"] = cid

def manual_scan(engine) -> str:
    """Manual scan: just report the current picture; never spawns."""
    sx, sy = engine._ship_xy()
    locked = engine.state.get("radar", {}).get("locked_contact_id")
    return status_line(engine.pool, (sx, sy), locked_id=locked)

# ------------------------------ Fleet (persistent) ---------------------------

# Two permanent contacts outside the non-persistent cap
FLEET_IDS: Dict[str, Optional[int]] = {"hermes": None, "coventry": None}

def _within_cells(c1: Tuple[float, float], c2: Tuple[float, float], max_cells: float) -> bool:
    dx = c1[0] - c2[0]; dy = c1[1] - c2[1]
    return math.hypot(dx, dy) <= max_cells

def _soft_set_course_speed(c, course_deg: float, speed_kts: float) -> None:
    try:
        c.course_deg = float(course_deg) % 360.0
        c.speed_kts_game = float(speed_kts)
    except Exception:
        pass

def _move_towards_cell(c, target_cell_xy: Tuple[float, float], max_step_cells: float = 1.2) -> None:
    tx, ty = target_cell_xy
    dx = tx - c.x; dy = ty - c.y
    dist = math.hypot(dx, dy)
    if dist <= 1e-6: return
    step = min(max_step_cells, dist)
    c.x += (dx / dist) * step
    c.y += (dy / dist) * step

def _spawn_from_pool(engine):
    """Call whatever spawn routine the pool exposes; return new contact or None."""
    pool = engine.pool
    for name in ("spawn_contact", "spawn_random_contact", "spawn", "add_random_contact"):
        fn = getattr(pool, name, None)
        if callable(fn):
            try:
                return fn()
            except TypeError:
                continue
    return None

def _pool_contacts_list(pool) -> list:
    """Ensure pool.contacts exists and is a list; return it."""
    if not hasattr(pool, "contacts") or getattr(pool, "contacts") is None:
        try:
            setattr(pool, "contacts", [])
        except Exception:
            pass
    lst = getattr(pool, "contacts", None)
    if not isinstance(lst, list):
        try:
            lst = list(lst)  # best-effort
        except Exception:
            lst = []
        try:
            setattr(pool, "contacts", lst)
        except Exception:
            pass
    return lst

def _next_contact_id(pool) -> int:
    lst = _pool_contacts_list(pool)
    try:
        max_id = max(int(getattr(c, "id", 0) or 0) for c in lst) if lst else 0
    except Exception:
        max_id = 0
    return max(1, max_id + 1)

def _fabricate_contact(engine, *, name: str, type_label: str, allegiance: str,
                       cell_xy: tuple[float, float], course: float, speed: float):
    """
    Minimal, safe contact object if pool can't spawn and we have nothing to promote.
    Adds to pool via add_contact(...) if present, else appends to pool.contacts list.
    """
    pool = engine.pool
    lst = _pool_contacts_list(pool)
    cid = _next_contact_id(pool)
    x, y = cell_xy

    # Try real Contact class; if it fails, FALL BACK to SimpleNamespace
    C = getattr(contacts, "Contact", None)
    obj = None
    if C is not None:
        try:
            obj = C(
                id=cid, x=float(x), y=float(y),
                type=type_label, allegiance=allegiance,
                course_deg=float(course) % 360.0, speed_kts_game=float(speed),
                name=name
            )
        except Exception:
            obj = None

    if obj is None:
        obj = _NS(
            id=cid, x=float(x), y=float(y),
            type=type_label, allegiance=allegiance, name=name,
            course_deg=float(course) % 360.0, speed_kts_game=float(speed),
        )

    # Prefer add_contact if available
    add_fn = getattr(pool, "add_contact", None)
    if callable(add_fn):
        try:
            add_fn(obj)
            return obj
        except Exception:
            pass

    # Fallback: append to pool.contacts list
    try:
        lst.append(obj)
        return obj
    except Exception:
        return None

def _find_by_id(engine, cid: Optional[int]):
    if cid is None: return None
    return next((c for c in getattr(engine.pool, "contacts", []) if c.id == cid), None)

def _is_persistent(c) -> bool:
    return c.id in {v for v in FLEET_IDS.values() if v is not None}

def _ensure_one_persistent(engine, key: str, title: str, type_label: str,
                           anchor_xy: Tuple[float, float], logs: List[str]) -> None:
    """
    Guarantee a named, friendly contact exists near anchor_xy, outside the cap.
    Order:
      1) keep existing
      2) try pool spawn -> mark Friendly
      3) promote existing Friendly ship
      4) fabricate directly
    """
    cid = FLEET_IDS.get(key)
    obj = _find_by_id(engine, cid)

    if obj is None:
        # 2) try a fresh spawn
        obj = _spawn_from_pool(engine)
        if obj is not None:
            try:
                obj.allegiance = "Friendly"
            except Exception:
                pass

        # 3) promote an existing non-air friendly
        if obj is None:
            candidates = [c for c in _pool_contacts_list(engine.pool)
                          if getattr(c, "allegiance", "") == "Friendly"
                          and not _is_persistent(c)
                          and all(t not in (getattr(c, "type", "") or "") for t in ("Skyhawk", "Mirage", "Aircraft"))]
            if candidates:
                obj = candidates[0]

        # 4) fabricate if still none
        if obj is None:
            course, speed = engine._ship_course_speed()
            ax, ay = anchor_xy
            fx, fy = ax + random.uniform(-0.5, 0.5), ay + random.uniform(-0.5, 0.5)
            obj = _fabricate_contact(
                engine,
                name=title, type_label=type_label, allegiance="Friendly",
                cell_xy=(fx, fy), course=course, speed=speed
            )
            if obj is None:
                logs.append("Radar: fleet assignment failed (spawn/promote/fabricate all failed to add to pool)")
                return

        # Stamp identity + placement
        FLEET_IDS[key] = obj.id
        obj.allegiance = "Friendly"
        obj.type = type_label
        obj.name = title
        ax, ay = anchor_xy
        obj.x = ax + random.uniform(-0.5, 0.5)
        obj.y = ay + random.uniform(-0.5, 0.5)
        logs.append(f"Fleet: assigned #{obj.id} {obj.name} as persistent friendly")

    # Keep formation (vector + proximity)
    course, speed = engine._ship_course_speed()
    _soft_set_course_speed(obj, course, speed)
    ax, ay = anchor_xy
    if not _within_cells((obj.x, obj.y), (ax, ay), 3.0):
        _move_towards_cell(obj, (ax, ay), max_step_cells=1.2)

def _ensure_fleet_presence(engine) -> List[str]:
    logs: List[str] = []
    sx, sy = engine._ship_xy()
    # anchors relative to own ship (cells)
    hermes_anchor = (sx - 2.0, sy + 1.5)
    coventry_anchor = (sx + 2.0, sy - 1.5)
    _ensure_one_persistent(engine, "hermes", "HMS Hermes", "Carrier", hermes_anchor, logs)
    _ensure_one_persistent(engine, "coventry", "HMS Coventry", "Type 21 Frigate", coventry_anchor, logs)
    return logs

# --------------------------- Non-persistent spawn rhythm ---------------------

# Timers (seconds from 'now' until next attempt)
_next_friendly_delay: float = 0.0
_next_hostile_delay: float = 0.0
_last_friendly_ts: float = 0.0
_last_hostile_ts: float = 0.0

# Back-off for diagnostics (to avoid spam)
_last_diag_ts: float = 0.0

def _cfg(engine) -> Dict:
    return engine.game_cfg.get("radar", {}) or {}

def _counts_nonpersistent(engine) -> Tuple[int, int, int]:
    """Return (total_nonpersistent, friendlies_nonpersistent, hostiles_nonpersistent)."""
    np = [c for c in _pool_contacts_list(engine.pool) if not _is_persistent(c)]
    fr = sum(1 for c in np if getattr(c, "allegiance", "") == "Friendly")
    ho = sum(1 for c in np if getattr(c, "allegiance", "") == "Hostile")
    return len(np), fr, ho

def _spawn_one(engine, allegiance_hint: Optional[str], logs: List[str]):
    c = _spawn_from_pool(engine)
    if c is None:
        global _last_diag_ts
        now = time.time()
        if now - _last_diag_ts > 30.0:
            _last_diag_ts = now
            logs.append("Radar: no spawn() method available on pool (or call failed)")
        return None
    try:
        if allegiance_hint and getattr(c, "allegiance", None) != allegiance_hint:
            c.allegiance = allegiance_hint
    except Exception:
        pass
    # ensure contacts list exists
    _ = _pool_contacts_list(engine.pool)
    return c

def _schedule_next(min_s: int, max_s: int) -> float:
    return float(random.randint(min_s, max_s))

# ------------------------------ Auto tick ------------------------------------

def auto_tick(engine, now: float) -> List[str]:
    """
    Called each engine tick; maintains fleet and spawns non-persistent contacts.
    Returns log lines for new contacts and diagnostics.
    """
    global _next_friendly_delay, _next_hostile_delay, _last_friendly_ts, _last_hostile_ts

    logs: List[str] = []

    # Maintain fleet (outside cap) — guaranteed by fabricate fallback
    logs += _ensure_fleet_presence(engine)

    # Read config
    cfg = _cfg(engine)
    max_contacts = int(cfg.get("max_contacts", 10))

    f_cfg = cfg.get("friendlies", {}) or {}
    h_cfg = cfg.get("hostiles", {}) or {}

    # Defaults fast enough for testing
    f_min = int(f_cfg.get("interval_min", 20))
    f_max = int(f_cfg.get("interval_max", 45))
    f_cap = int(f_cfg.get("max_active", 4))

    h_min = int(h_cfg.get("interval_min", 15))
    h_max = int(h_cfg.get("interval_max", 40))
    h_cap = int(h_cfg.get("max_active", 6))
    wave_p = float(h_cfg.get("wave_chance", 0.6))
    wave_min = int(h_cfg.get("wave_size_min", 3))
    wave_max = int(h_cfg.get("wave_size_max", 5))

    # Non-persistent cap
    total_np, fr_np, ho_np = _counts_nonpersistent(engine)
    if total_np >= max_contacts:
        logs.append(f"Radar: non-persistent cap reached ({total_np}/{max_contacts})")
        return logs

    sx, sy = engine._ship_xy()

    # Friendly trickle
    if now - _last_friendly_ts >= _next_friendly_delay:
        if fr_np < f_cap and total_np < max_contacts:
            c = _spawn_one(engine, "Friendly", logs)
            if c and not _is_persistent(c):
                rng = round(contacts.dist_nm_xy(c.x, c.y, sx, sy, engine.pool.grid), 1)
                cell = contacts.format_cell(int(round(c.x)), int(round(c.y)))
                logs.append(f"NEW CONTACT: {cell} {c.type} ({c.allegiance}) d={rng}nm crs {c.course_deg:.0f}° {c.speed_kts_game:.0f}kts")
                total_np += 1; fr_np += 1
        _last_friendly_ts = now
        _next_friendly_delay = _schedule_next(f_min, f_max)

    # Hostile trickle/wave
    if now - _last_hostile_ts >= _next_hostile_delay:
        qty = 1
        if random.random() < wave_p:
            qty = random.randint(wave_min, wave_max)
        spawned = 0
        for _ in range(qty):
            if ho_np >= h_cap or total_np >= max_contacts:
                break
            c = _spawn_one(engine, "Hostile", logs)
            if c and not _is_persistent(c):
                rng = round(contacts.dist_nm_xy(c.x, c.y, sx, sy, engine.pool.grid), 1)
                cell = contacts.format_cell(int(round(c.x)), int(round(c.y)))
                logs.append(f"NEW CONTACT: {cell} {c.type} ({c.allegiance}) d={rng}nm crs {c.course_deg:.0f}° {c.speed_kts_game:.0f}kts")
                total_np += 1; ho_np += 1; spawned += 1
        if spawned == 0 and ho_np >= h_cap:
            logs.append(f"Radar: hostile faction at cap ({ho_np}/{h_cap})")
        _last_hostile_ts = now
        _next_hostile_delay = _schedule_next(h_min, h_max)

    return logs